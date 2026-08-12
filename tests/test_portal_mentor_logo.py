"""The persistent mentor launcher uses the established Mentorship University mark."""
from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "client-portal.html").read_text()


def test_mentor_launcher_uses_existing_fleur_de_lis_logo():
    launcher = HTML.split('id="mentorLauncher"', 1)[1].split("</button>", 1)[0]
    assert 'src="/static/logo.png"' in launcher
    assert 'class="mentor-launcher-logo"' in launcher
    assert "MU</span>" not in launcher
    assert 'aria-label="Open Mentorship University mentor"' in HTML
