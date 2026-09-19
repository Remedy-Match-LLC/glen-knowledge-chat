"""No client-facing page may say "voice scan" unqualified.

Glen, 2026-09-18. There are two instruments and the bare phrase lets a client
believe they got the other one:

    Bioenergetic Wellness Scan   Energy4Life's. Ten seconds, counting to ten.
    Five Element Voice Scan      Glen's own.

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
    "Five Element Voice Scan",
    # Energy4Life's own menu label, quoted as theirs on begin-scan. Renaming it would
    # send a client hunting for a button that does not exist in their app.
    '"Voice Scan"',
)

# One documented exception, not a silent skip. begin-voice.html offers a
# "30-second bioenergetic voice scan", and 30 seconds contradicts the ten seconds
# every other surface and the glen-client-copy skill give for the Bioenergetic
# Wellness Scan. Until Glen says which instrument that card is, renaming it would
# attach a wrong duration to a named instrument, which is worse than leaving it
# vague. Remove this entry once he rules.
PENDING = {"begin-voice.html"}


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
        "Wellness Scan, Glen's own is the Five Element Voice Scan:\n"
        + "\n".join(f"  {o}" for o in offenders))


def test_the_pending_exception_is_still_real():
    """A skip that quietly stops applying is worse than no skip. If begin-voice no
    longer has a bare use, this test fails and the entry must come out of PENDING."""
    p = STATIC / "begin-voice.html"
    if not p.exists():
        return
    assert _bare_uses(p.read_text(encoding="utf-8")), \
        "begin-voice.html is clean now. Remove it from PENDING."


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
