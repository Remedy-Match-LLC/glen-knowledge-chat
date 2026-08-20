import console_push_cron as cron


class _Request:
    def __init__(self, payload): self.payload = payload
    def execute(self): return self.payload


class _CalendarList:
    def list(self):
        return _Request({"items": [{"id": "cal-1", "summary": "Group Coaching"}]})


class _Events:
    def __init__(self): self.page_tokens = []
    def list(self, **kwargs):
        token = kwargs.get("pageToken")
        self.page_tokens.append(token)
        event_id = "group-next-week" if token else "group-this-week"
        payload = {"items": [{
            "id": event_id, "summary": "Group Coaching",
            "start": {"dateTime": "2099-01-01T10:00:00-10:00"},
            "end": {"dateTime": "2099-01-01T11:00:00-10:00"},
        }]}
        if token is None: payload["nextPageToken"] = "page-2"
        return _Request(payload)


class _Service:
    def __init__(self): self.event_api = _Events()
    def calendarList(self): return _CalendarList()
    def events(self): return self.event_api


class _Response:
    def json(self): return {"upserted": 2}


def test_calendar_sync_fetches_all_result_pages(monkeypatch):
    service = _Service()
    posted = {}
    monkeypatch.setattr(cron, "_cal_service", lambda: service)
    monkeypatch.setattr(
        cron.requests, "post",
        lambda url, headers, json, timeout: posted.update(events=json) or _Response())

    cron.push_calendar_events(days=14)

    assert service.event_api.page_tokens == [None, "page-2"]
    assert [event["google_event_id"] for event in posted["events"]] == [
        "group-this-week", "group-next-week"]
