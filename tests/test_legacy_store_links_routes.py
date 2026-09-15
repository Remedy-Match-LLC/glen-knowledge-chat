"""A streamed chat route rewrites an old store link the model splits across deltas.

Uses the fireside agent route: it streams through the same stream_visible path as the main
chat and persists the answer, so it shows both what the client saw and what was stored.
These need the full app import, so only CI can judge them.
"""
import importlib
import json
import re
import sqlite3


def _reload_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIRESIDE_ENABLED", "true")
    import app as appmod
    importlib.reload(appmod)
    return appmod


class _FakeStream:
    def __init__(self, toks): self._toks = toks
    def __enter__(self): return self
    def __exit__(self, *a): return False

    @property
    def text_stream(self):
        for t in self._toks:
            yield t


class _FakeMessages:
    def __init__(self, toks): self._toks = toks
    def stream(self, **kw): return _FakeStream(self._toks)


class _FakeCl:
    def __init__(self, toks): self.messages = _FakeMessages(toks)


def _tokens(body):
    return "".join(json.loads(m)["token"]
                   for m in re.findall(r'data: (\{"token":.*?\})\n\n', body))


def _post(appmod, message, sess="linksess"):
    return appmod.app.test_client(use_cookies=False).post(
        "/begin/fireside/agent", json={"message": message},
        headers={"Cookie": "amg_session=" + sess})


def test_split_store_link_is_rewritten_in_stream_and_in_storage(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    monkeypatch.setattr(appmod, "_fireside_coverage_async", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cl", _FakeCl([
        "Try [Nous Energy](https://remedy",
        "match.com/remedies/syntropy/85-nous-",
        "energy) and write support@remedymatch.com.",
    ]))
    body = _post(appmod, "I feel flat").get_data(as_text=True)
    shown = _tokens(body)
    new = f"{appmod.PUBLIC_BASE_URL}/begin/product/nous-energy"
    assert shown == f"Try [Nous Energy]({new}) and write support@remedymatch.com."

    from dashboard import fireside_store as fs
    with sqlite3.connect(appmod.LOG_DB) as cx:
        s = fs.get_or_create(cx, "linksess")
    stored = [t["text"] for t in s["transcript"] if t["speaker"] == "glendalf"]
    assert stored == [shown]


def test_unmapped_store_link_is_removed_keeping_its_text(monkeypatch, tmp_path):
    appmod = _reload_app(monkeypatch, tmp_path)
    monkeypatch.setattr(appmod, "_fireside_coverage_async", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cl", _FakeCl([
        "Skip [EMM](https://remedymatch.com/remedies/syntropy/542-elec",
        "trolyte-mineral-manna).",
    ]))
    shown = _tokens(_post(appmod, "salts?", sess="s2").get_data(as_text=True))
    assert shown == "Skip EMM."
