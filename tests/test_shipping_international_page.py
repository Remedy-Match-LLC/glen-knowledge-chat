import importlib
import sys
from pathlib import Path

import pytest


def _load_app():
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        return importlib.import_module("app")
    except Exception as e:
        pytest.skip(f"app module not importable in this env: {e}")


@pytest.mark.parametrize("host", ["myhealingoasis.com", "remedymatch.com"])
def test_shipping_international_served_200(host):
    app_module = _load_app()
    r = app_module.app.test_client().get("/shipping/international", base_url=f"https://{host}")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("Content-Type", "")
    body = r.get_data(as_text=True)
    assert "<h1>Getting your remedies outside the US</h1>" in body


def test_shipping_international_carries_the_approved_text():
    app_module = _load_app()
    body = app_module.app.test_client().get("/shipping/international").get_data(as_text=True)
    assert "<title>Ordering Remedies From Outside the US</title>" in body
    assert "Our store ships to addresses in the United States only." in body
    assert "If you have family or a friend in the United States" in body
    assert "They can then carry your remedies home in their luggage." in body
    assert "https://forward2me.com" in body
    assert "(808) 217-9647" in body


def test_shipping_international_leaves_out_the_draft_notes():
    # Glen approved the page without the customs-fee line and without the draft's notes to him.
    app_module = _load_app()
    r = app_module.app.test_client().get("/shipping/international")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    for held in ("HELD", "For Glen", "customs fees", "Draft 1", "MyUS", "PostScan"):
        assert held not in body
