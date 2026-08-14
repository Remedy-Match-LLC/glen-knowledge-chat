from pathlib import Path


SHELL = (Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text()


def test_portal_shell_offers_safe_return_to_console_list():
    assert 'get("from") === "console"' in SHELL
    assert "https://illtowell.com/console/portal-links" in SHELL
    assert "Back to client list" in SHELL
