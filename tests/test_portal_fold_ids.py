"""Every portal card carries a fold id, so a fold is keyed on a fixed name and not on
heading text (the 2026-09-16 defect). Runtime uniqueness is checked in the browser
test, because loop-built ids only exist once rendered."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = [ROOT / "static" / "client-portal.html", ROOT / "static" / "portal-mentor.js",
         *sorted((ROOT / "static" / "js").glob("portal-*.js"))]

# An opening tag whose class list starts with the bare word card, in HTML or inside a
# JS string: class="card", class="card quiet", class=\"card x\", class='card'.
CARD_CLASS = re.compile(r"""class=\\?(["'])card(?:\s[^"'\\]*)?\\?\1""")


def _strip_comments(src):
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


def _card_tags(src):
    for m in CARD_CLASS.finditer(src):
        start = src.rfind("<", 0, m.start())
        end = src.find(">", m.end())
        yield src[start:end + 1]


def test_every_card_tag_has_a_fold_id():
    missing = []
    for f in FILES:
        for tag in _card_tags(_strip_comments(f.read_text())):
            if "data-fold-id=" not in tag and not re.search(r"""\sid=\\?["']""", tag):
                missing.append(f"{f.name}: {tag[:120]}")
    assert not missing, "cards with no fold id:\n" + "\n".join(missing)


def test_script_built_cards_get_a_fold_id():
    page = (ROOT / "static" / "client-portal.html").read_text()
    for var in ("warning", "upsell"):
        assert re.search(rf"{var}\.dataset\.foldId\s*=", page), var


def test_the_scan_finds_cards_at_all():
    # A clean zero here would mean the regex went blind, not that all is well.
    n = sum(1 for f in FILES for _ in _card_tags(_strip_comments(f.read_text())))
    assert n >= 80, n


def test_live_events_card_is_skipped():
    page = (ROOT / "static" / "client-portal.html").read_text()
    assert re.search(r'class="card calendar-summary"[^>]*data-fold-skip="1"', page)
