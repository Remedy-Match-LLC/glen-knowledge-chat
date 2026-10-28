"""Voice-measurement paragraphs on the privacy page, approved by Glen on 2026-10-12.

Three sentences, one paragraph each. The first carries a bold "Voice measurements." lead,
matching "Credentials." and "Payment Data.". They sit after "Payment Data." and before
"All personal information that you provide...". Glen said go live on 2026-10-21.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRIVACY = ROOT / "static" / "privacy.html"

SENTENCES = [
    "When you choose to keep them, we store a few measurements of how your voice sounds, such as pitch and loudness.",
    "We delete the recording itself as soon as it is analysed.",
    "We never use these measurements to identify you.",
]

# Voice clips, approved by Glen on 2026-10-27. Describes PR 2160 (deleted 90 days after
# sending). The erasure sentence is held until client erasure is switched on (Glen 10-27).
CLIP_SENTENCES = [
    "We delete a voice clip you attach to an appointment message 90 days after you send it.",
]


# Five Element Voice Analysis opt-in, approved by Glen on 2026-10-28. Describes PR 2159
# (the server refuses a recording without the tick; unticking deletes the voice pattern).
# Narrowed by Glen on 10-28 after review: tone measurements are not built, so none are named.
OPT_IN_SENTENCES = [
    "The analysis runs only after you tick the box to agree.",
    "When you tick it, we keep the five element pattern from your recording.",
    "You can untick the box at any time, and we delete that pattern.",
]


def _paragraphs():
    html = re.sub(r"<!--.*?-->", "", PRIVACY.read_text(), flags=re.S)
    return re.findall(r"<p>(.*?)</p>", html, flags=re.S)


def test_three_sentences_each_own_paragraph_in_place():
    paras = _paragraphs()
    pay = next(i for i, p in enumerate(paras) if p.startswith("<strong>Payment Data.</strong>"))
    assert paras[pay + 1] == "<strong>Voice measurements.</strong> " + SENTENCES[0]
    assert paras[pay + 2] == SENTENCES[1]
    assert paras[pay + 3] == SENTENCES[2]
    assert paras[pay + 4] == "<strong>Five Element Voice Analysis.</strong> " + OPT_IN_SENTENCES[0]
    assert paras[pay + 5] == OPT_IN_SENTENCES[1]
    assert paras[pay + 6] == OPT_IN_SENTENCES[2]
    assert paras[pay + 7] == "<strong>Voice clips.</strong> " + CLIP_SENTENCES[0]
    assert paras[pay + 8].startswith("All personal information that you provide to us")


def test_voice_region_holds_only_plain_paragraphs():
    """A wrapper such as <div hidden>, <template> or <details> around these paragraphs
    would hide them while the paragraph checks pass (review round 3, 2026-10-28)."""
    html = re.sub(r"<!--.*?-->", "", PRIVACY.read_text(), flags=re.S)
    start = html.index("<strong>Payment Data.</strong>")
    start = html.index("</p>", start) + len("</p>")
    end = html.index("<p>All personal information that you provide to us")
    region = re.sub(r"<p>.*?</p>", "", html[start:end], flags=re.S)
    assert region.strip() == "", region
    assert not re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", html, flags=re.I)
    # A wrapper can also open before "Payment Data." and close after the region
    # (review round 2), so the page as a whole may carry no hiding markup at all.
    assert not re.search(r"<[a-z][^>]*\shidden[\s>=]|<template|<details", html, flags=re.I)


def test_heading_and_each_sentence_appear_once():
    html = re.sub(r"<!--.*?-->", "", PRIVACY.read_text(), flags=re.S)
    for text in (["Voice measurements.", "Five Element Voice Analysis.", "Voice clips."]
                 + SENTENCES + OPT_IN_SENTENCES + CLIP_SENTENCES):
        assert html.count(text) == 1, text
