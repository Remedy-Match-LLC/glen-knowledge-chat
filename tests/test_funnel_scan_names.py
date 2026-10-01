"""Funnel copy names the Energy4Life scan, and lists what its signup asks for.

Glen, 2026-09-18: the Energy4Life scan is the Bioenergetic Wellness Scan, "voice scan" never
appears unqualified, and "Bioenergetic Voice Analysis" means the same scan. Glen, 2026-09-30:
"DOB, gender, species, phone, address are required for e4l account as well."
"""
import re
from pathlib import Path

FILES = ("static/begin.html", "begin_funnel.py", "static/body-map.js")


def _client_text(path):
    src = Path(path).read_text()
    if path == "begin_funnel.py":   # the matcher's keyword list is not copy
        src = re.sub(r'"voice scan", "bioenergetic"\]', "", src)
    return src


def test_no_bare_voice_scan_or_retired_name():
    for f in FILES:
        low = _client_text(f).lower()
        bare = [m.start() for m in re.finditer(r"(?<!e4l )(?<!five element )voice scan", low)]
        assert not bare, (f, bare)
        assert "bioenergetic voice analysis" not in low, f


def test_the_scan_is_named():
    assert '<div class="eyebrow-k">Bioenergetic Wellness Scan</div>' in Path("static/begin.html").read_text()
    assert '"Start your Bioenergetic Wellness Scan"' in Path("begin_funnel.py").read_text()


def test_signup_fields_include_what_energy4life_requires():
    html = Path("static/begin-scan.html").read_text()
    i = html.index("Their signup asks for your")
    fields = re.sub(r"\s+", " ", html[i:i + 200])
    for f in ("name", "email", "date of birth", "gender", "species", "country", "address",
              "phone number", "password"):
        assert f in fields, f


def test_the_chat_prompt_uses_the_full_scan_name():
    """Glen 2026-09-26: "E4L voice scan" is stale; "(voice scan)" only follows the full name."""
    src = Path("app.py").read_text()
    # The one docstring that still says it is a comment, not prompt text.
    assert src.count("E4L voice scan") == 1
    assert "The free E4L voice scan does NOT qualify" in src
    assert "Energy4Life (E4L) Bioenergetic Wellness Scan (voice scan)" in src
