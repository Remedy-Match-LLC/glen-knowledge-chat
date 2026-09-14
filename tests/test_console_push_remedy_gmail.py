"""Mail to support@, drglen@ and rae@ becomes console todos via Glen's Gmail.

2026-09-14: remedymatch.com mail moved to Google Workspace and is forwarded to Glen's Gmail.
The GrooveKart IMAP pulls that used to make these todos were retired. Aliases keep their own
Delivered-To, so the search must name all three addresses. Nothing may mark mail read.
"""
import console_push_cron as cron


class _Request:
    def __init__(self, payload): self.payload = payload
    def execute(self): return self.payload


class _Messages:
    """Only list and get exist, so a call to modify, trash or send raises."""

    def __init__(self, found):
        self.found = found
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return _Request({"messages": [{"id": mid} for mid in self.found]})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        mid = kwargs["id"]
        sender, subject = self.found[mid]
        return _Request({
            "internalDate": "0", "snippet": "hello",
            "payload": {"headers": [{"name": "From", "value": sender},
                                    {"name": "Subject", "value": subject}],
                        "mimeType": "text/plain", "body": {"data": ""}},
        })


class _Users:
    def __init__(self, messages): self._messages = messages
    def messages(self): return self._messages


class _Service:
    def __init__(self, found):
        self.messages_api = _Messages(found)
    def users(self): return _Users(self.messages_api)


FOUND = {
    "m-client": ("A Client <client@gmail.com>", "Question about my scan"),
    "m-vendor": ("Vendor <sales@vendor-co.com>", "Wholesale pricing"),
    "m-order": ("Store <orders@vendor-co.com>", "New order #1032"),
}


def _no_ai(monkeypatch):
    monkeypatch.setattr(cron, "_bullet_summary", lambda s, f, b: ("bullets", "action", "core"))


def test_query_names_all_three_addresses_and_only_unread():
    for address in ("support@remedymatch.com", "drglen@remedymatch.com", "rae@remedymatch.com"):
        assert f"deliveredto:{address}" in cron.REMEDY_QUERY
    assert "is:unread" in cron.REMEDY_QUERY


def test_todos_keep_the_support_shape_and_owner_split(monkeypatch):
    _no_ai(monkeypatch)
    service = _Service(FOUND)

    todos = {t["dedup_key"]: t for t in cron.triage_remedy_gmail(service)}

    assert set(todos) == {"remedy:gmail:m-client", "remedy:gmail:m-vendor", "remedy:gmail:m-order"}
    assert todos["remedy:gmail:m-client"]["owner"] == "rae"      # personal-domain sender
    assert todos["remedy:gmail:m-vendor"]["owner"] == "glen"
    assert todos["remedy:gmail:m-order"]["owner"] == "rae"       # order subject
    for t in todos.values():
        assert t["category"] == "Remedy Match Support"
        assert t["priority"] == "high"
        assert t["source"] == "remedy-gmail"
    list_call = [kw for name, kw in service.messages_api.calls if name == "list"][0]
    assert list_call["q"].startswith(cron.REMEDY_QUERY)


def test_only_reads_are_made(monkeypatch):
    _no_ai(monkeypatch)
    service = _Service(FOUND)
    cron.triage_remedy_gmail(service)
    assert {name for name, _ in service.messages_api.calls} == {"list", "get"}


def test_grovekart_pulls_are_gone():
    for name in ("triage_remedy_imap", "triage_remedy_orders", "REMEDY_HOST", "REMEDY_PASSWORD"):
        assert not hasattr(cron, name)


def test_main_runs_the_gmail_search_with_glens_service(monkeypatch):
    class _Token:
        def exists(self): return True

    class _Missing:
        def exists(self): return False

    glen_service = object()
    seen, posted = [], []
    monkeypatch.setattr(cron, "GLEN_TOKEN", _Token())
    monkeypatch.setattr(cron, "RAE_TOKEN", _Missing())
    monkeypatch.setattr(cron, "_gmail_service", lambda *a, **k: glen_service)
    for name in ("triage_gmail", "triage_pb", "triage_starred", "fetch_ghl_tasks"):
        monkeypatch.setattr(cron, name, lambda *a, **k: [])
    for name in ("sync_people_from_ghl", "push_calendar_events", "process_delete_queue",
                 "push_projects_md", "push_task_board"):
        monkeypatch.setattr(cron, name, lambda *a, **k: None)
    monkeypatch.setattr(cron, "triage_remedy_gmail",
                        lambda service, **k: seen.append(service) or [{"title": "support mail"}])
    monkeypatch.setattr(cron, "_post_todos", lambda items: posted.extend(items))

    cron.main()

    assert seen == [glen_service]
    assert {"title": "support mail"} in posted
