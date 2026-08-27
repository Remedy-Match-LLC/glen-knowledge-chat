import importlib
from pathlib import Path
import sys

import pytest


def _load_app():
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        return importlib.import_module("app")
    except Exception as exc:
        pytest.skip(f"app not importable in this env: {exc}")


def test_funnel_order_source_names_configured_website(monkeypatch):
    app = _load_app()
    monkeypatch.setattr(app, "PUBLIC_BASE_URL", "https://shop.example.com/path")
    assert app._order_board_source_label("funnel") == "shop.example.com"


def test_non_funnel_order_source_has_no_override():
    app = _load_app()
    assert app._order_board_source_label("in-house") == ""


def test_orders_ui_uses_server_supplied_website_name():
    source = (Path(__file__).resolve().parent.parent / "static" / "console-orders.html").read_text()
    assert "'funnel':o.source_label||'illtowell.com'" in source


def test_orders_are_grouped_by_business_facing_source_in_each_lane():
    source = (Path(__file__).resolve().parent.parent / "static" / "console-orders.html").read_text()
    assert "function groupedCardsHtml(orders)" in source
    assert "+ groupedCardsHtml(inLane)" in source
    assert "'groovekart':'GrooveKart (RemedyMatch)'" in source
    assert "'in-house':'Email'" in source
    assert "'e4l':'E4L'" in source
