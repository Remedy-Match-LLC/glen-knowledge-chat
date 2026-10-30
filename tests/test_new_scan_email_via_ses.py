"""The new-scan notice goes through Amazon SES first, off GoHighLevel (Glen, 2026-10-29).

GHL runs only when SES surely sent nothing. A doubtful SES outcome sends no second copy.
The body holds a live portal link, so no log line carries it or the address.
"""
import importlib
import sys
from pathlib import Path

import pytest

TOKEN = "SECRET-PORTAL-TOKEN-7"


class FakeSES:
    def __init__(self):
        self.calls, self.fail = [], None

    def send_email(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail()
        return {"MessageId": "m-1"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import app as a
    importlib.reload(a)
    from dashboard import db, email_suppression as es, ghl_email, ses_mail
    with db.connect(a.LOG_DB) as cx:
        es.init_table(cx)
    monkeypatch.setenv("SES_SEND_ENABLED", "1")
    monkeypatch.setenv("SES_CONFIGURATION_SET", "remedymatch-events")
    fake = FakeSES()
    real = ses_mail.send
    monkeypatch.setattr(ses_mail, "send", lambda *x, **k: real(*x, client=fake, **k))
    ghl = []
    monkeypatch.setattr(ghl_email, "send_via_ghl", lambda to, *x, **k: ghl.append(to))
    return {"app": a, "ses": fake, "ghl": ghl, "es": es, "db": db}


def _send(rig, to="client@x.com"):
    rig["app"]._send_new_scan_email(to, "2026-10-29", 9, TOKEN)


def test_ses_sends_it_and_ghl_does_not(rig):
    _send(rig)
    assert len(rig["ses"].calls) == 1 and rig["ghl"] == []
    raw = rig["ses"].calls[0]["Content"]["Raw"]["Data"].decode()
    assert "To: client@x.com" in raw and TOKEN in raw
    assert "Reply-To: drglenswartwout@gmail.com" in raw
    assert "List-Unsubscribe" not in raw          # a notice about their own scan, as on GHL


def test_ses_off_keeps_ghl(rig, monkeypatch):
    monkeypatch.setenv("SES_SEND_ENABLED", "")
    _send(rig)
    assert rig["ses"].calls == [] and rig["ghl"] == ["client@x.com"]


def test_a_refusal_falls_back_to_ghl(rig):
    from botocore.exceptions import ClientError
    rig["ses"].fail = lambda: ClientError(
        {"Error": {"Code": "MessageRejected"}, "ResponseMetadata": {"HTTPStatusCode": 400}},
        "SendEmail")
    _send(rig)
    assert rig["ghl"] == ["client@x.com"]


def test_a_timeout_sends_no_second_copy(rig):
    from botocore.exceptions import ReadTimeoutError
    rig["ses"].fail = lambda: ReadTimeoutError(endpoint_url="https://x")
    _send(rig)
    assert len(rig["ses"].calls) == 1 and rig["ghl"] == []


def test_a_fault_in_the_ses_helper_falls_back_to_ghl(rig, monkeypatch):
    from dashboard import ses_mail
    monkeypatch.setattr(ses_mail, "deliver", lambda *a, **k: 1 / 0)
    _send(rig)
    assert rig["ghl"] == ["client@x.com"]


def test_a_suppressed_client_gets_nothing(rig):
    with rig["db"].connect(rig["app"].LOG_DB) as cx:
        rig["es"].add(cx, "client@x.com", "hard", "bounce", "test")
    _send(rig)
    assert rig["ses"].calls == [] and rig["ghl"] == []


def test_each_caregiver_gets_their_own_ses_copy(rig, monkeypatch):
    from dashboard import household
    monkeypatch.setattr(household, "cc_recipients_for", lambda cx, e: ["carer@x.com"])
    _send(rig)
    tos = [c["Content"]["Raw"]["Data"].decode().split("To: ")[1].split("\n")[0].strip()
           for c in rig["ses"].calls]
    assert tos == ["client@x.com", "carer@x.com"] and rig["ghl"] == []


def test_no_log_line_carries_the_link_or_the_address(rig, monkeypatch, capsys):
    from dashboard import email_suppression as es, ghl_email
    from botocore.exceptions import ClientError
    monkeypatch.setattr(es, "is_suppressed",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hidden.three")))
    rig["ses"].fail = lambda: ClientError(
        {"Error": {"Code": "MessageRejected", "Message": TOKEN},
         "ResponseMetadata": {"HTTPStatusCode": 400}}, "SendEmail")
    monkeypatch.setattr(ghl_email, "send_via_ghl",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(TOKEN)))
    _send(rig, "hidden.three@x.com")
    got = capsys.readouterr()
    assert TOKEN not in got.out + got.err and "hidden.three" not in got.out + got.err
