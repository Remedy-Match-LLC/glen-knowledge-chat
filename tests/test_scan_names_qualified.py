"""No client-facing page may say "voice scan" unqualified.

Glen, 2026-09-18. There are two instruments and the bare phrase lets a client
believe they got the other one:

    Bioenergetic Wellness Scan   Energy4Life's. Ten seconds, counting to ten.
    Five Element Voice Analysis  Glen's own. Renamed from "Five Element Voice Scan" on
                                 2026-09-26, so only Energy4Life's is ever a scan.

"Bioenergetic Voice Analysis" and "E4L voice scan" are stale labels for the FIRST
one, confirmed by Glen on the same day. Both were live.

Same failure mode as the Remedy Match / Biofield Analysis split: a loose name tells
someone they have something they do not have. Two pages had gone further and offered
a "Free Biofield(tm) voice scan" and a "biofield voice scan", putting the PAID
analysis's name on the free instrument.
"""
import pathlib
import re

STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"

# Full names that legitimately contain the words. Stripped before the check, so the
# guard sees only the bare uses.
ALLOWED = (
    # Energy4Life's own menu label, quoted as theirs on begin-scan. Renaming it would
    # send a client hunting for a button that does not exist in their app.
    '"Voice Scan"',
)

# begin-voice.html used to sit here as the one exception, because it offered a
# "30-second" scan. Glen ruled 2026-09-18: "The scan itself is a 10 second voice
# sample counting out loud from one to 10." It is renamed, so nothing is exempt.
PENDING = set()


def _pages():
    for p in sorted(STATIC.glob("*.html")):
        # Console and admin pages are staff-facing; the rule is about client copy.
        if p.name.startswith(("console-", "admin-")):
            continue
        yield p


def _bare_uses(text):
    for allowed in ALLOWED:
        text = text.replace(allowed, "")
    return re.findall(r".{0,60}voice scan.{0,40}", text, re.I)


def test_no_client_page_says_voice_scan_unqualified():
    offenders = []
    for p in _pages():
        if p.name in PENDING:
            continue
        for hit in _bare_uses(p.read_text(encoding="utf-8")):
            offenders.append(f"{p.name}: ...{hit.strip()}...")
    assert not offenders, (
        "Glen 2026-09-18: name the instrument. Energy4Life's is the Bioenergetic "
        "Wellness Scan, Glen's own is the Five Element Voice Analysis:\n"
        + "\n".join(f"  {o}" for o in offenders))


# A sentence that names Energy4Life's scan and gives it 30 seconds. Glen's own Five
# Element Voice Scan legitimately asks for about a minute, so the check is per LINE,
# and only on lines that name the Energy4Life instrument.
_E4L = re.compile(r"Bioenergetic Wellness Scan|Truly\.VIP/E4L|Energy4Life", re.I)
_THIRTY = re.compile(r"\b(30|thirty)[ -]seconds?\b", re.I)


def _thirty_second_claims(text):
    return [ln.strip()[:160] for ln in text.splitlines()
            if _E4L.search(ln) and _THIRTY.search(ln)]


def test_the_energy4life_scan_is_ten_seconds():
    """Glen 2026-09-18: a 10 second voice sample, counting out loud from one to 10.
    Pages said 30, and so did the chatbot's instructions in app.py."""
    sources = list(_pages()) + [STATIC.parent / "app.py"]
    offenders = [f"{p.name}: {hit}" for p in sources
                 for hit in _thirty_second_claims(p.read_text(encoding="utf-8"))]
    assert not offenders, "\n".join(offenders)


def test_the_duration_check_can_fire():
    """A control: the check must catch the exact wording that was live."""
    assert _thirty_second_claims(
        '<p>A quick Bioenergetic Wellness Scan reads you. It takes about 30 seconds.</p>')
    assert not _thirty_second_claims(
        "<p>Speak naturally for 30 seconds.</p><h2>Five Element Voice Analysis</h2>")


def test_the_renamed_pages_carry_the_real_name():
    """A control. The guard above is satisfied by DELETING the phrase, so pin that
    the pages actually name the instrument."""
    for name in ("begin-scan.html", "membership-choose.html", "biofield-ready.html"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert "Bioenergetic Wellness Scan" in text, f"{name} lost the instrument name"


def test_the_paid_name_is_not_on_the_free_scan():
    """Glen 2026-09-10 reserves "Biofield" for the paid manual analysis. Two pages
    had attached it to the free scan, which is how a client comes to believe they
    received something they were never given."""
    offenders = []
    for p in _pages():
        text = p.read_text(encoding="utf-8")
        for hit in re.findall(r"biofield[™\s]*voice scan", text, re.I):
            offenders.append(f"{p.name}: {hit!r}")
    assert not offenders, "the paid name is on the free scan: " + "; ".join(offenders)


def test_portal_step_list_names_the_energy4life_scan():
    """The portal Home step list said "Voice analysis" for the step that links to
    Energy4Life. Found 2026-09-22 on a captured portal. Read from the source rather
    than a render, because the step list is built server-side."""
    src = (STATIC.parent / "dashboard" / "portal_onboarding.py").read_text(encoding="utf-8")
    labels = re.findall(r'step\("voice",\s*"([^"]+)"', src)
    assert labels == ["Bioenergetic Wellness Scan"], labels


def test_glens_instrument_carries_its_new_name():
    """Glen, 2026-09-26: "Update name to Five Element Voice Analysis." A control, since
    the guard above passes if the name is simply deleted."""
    text = (STATIC / "client-portal.html").read_text(encoding="utf-8")
    assert "Five Element Voice Analysis" in text
    assert "Five Element Voice Scan" not in text
