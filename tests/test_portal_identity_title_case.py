"""The client portal identity follows the mixed/title-case brand style."""
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "client-portal.html").read_text()


def test_my_healing_oasis_is_rendered_in_title_case():
    assert '<span class="portal-client-identity-label">My Healing Oasis</span>' in HTML
    rule = HTML.split(".portal-client-identity-label{", 1)[1].split("}", 1)[0]
    assert "text-transform:none" in rule
    assert "text-transform:uppercase" not in rule
