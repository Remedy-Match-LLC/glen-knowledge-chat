"""Reference colour of a powder product's latest batch, for its product page.

Glen, 2026-11-16: "Also put the color on each product page for reference. It can be an
average color of the image rather than the original image." The production pillar
records each batch's average photo colour in data/powder-colours.json:

    {"reverse-age": [{"hex": "#a37850", "batch_date": "2026-11-16"}]}

A new batch is one more entry appended to the slug's list. The latest batch_date wins,
whatever order the entries are in. An entry with a bad hex or date is skipped, never
raised, so one hand-typing slip cannot break a product page. Keys starting with "_" are
notes for whoever edits the file.
"""
import json
import os
import re
from datetime import date

_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "powder-colours.json")
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")

_cache = {"key": None, "data": {}}


def _load(path=None):
    path = path or _PATH
    try:
        st = os.stat(path)
        mtime = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _cache["key"] == (path, mtime):
        return _cache["data"]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"[powder-colours] could not load {path}: {e!r}", flush=True)
        data = {}
    if not isinstance(data, dict):
        data = {}
    _cache.update(key=(path, mtime), data=data)
    return data


def _valid(entry):
    """(date, hex) for a well-formed entry, else None."""
    if not isinstance(entry, dict):
        return None
    hx, bd = entry.get("hex"), entry.get("batch_date")
    if not (isinstance(hx, str) and _HEX.fullmatch(hx)):
        return None
    if not (isinstance(bd, str) and _DATE.fullmatch(bd)):
        return None
    try:
        d = date.fromisoformat(bd)
    except ValueError:
        return None
    return d, hx.lower()


def label_for(d):
    """Sentence-case label, e.g. 'Reference colour, batch of 16 November 2026'."""
    return f"Reference colour, batch of {d.day} {_MONTHS[d.month - 1]} {d.year}"


def colour_for(slug, path=None):
    """{"hex", "batch_date", "label"} for the slug's latest valid batch, or None."""
    if not slug or str(slug).startswith("_"):
        return None
    entries = _load(path).get(slug)
    if not isinstance(entries, list):
        return None
    valid = [v for v in (_valid(e) for e in entries) if v]
    if not valid:
        return None
    d, hx = max(valid, key=lambda v: v[0])
    return {"hex": hx, "batch_date": d.isoformat(), "label": label_for(d)}
