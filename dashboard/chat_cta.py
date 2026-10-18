"""Pure parser for the trailing CTA directive a brief answer emits.
Grammar: a final line  ⟦CTA⟧ <type> | <target> | <label>
type ∈ page|email|action|inline. Flask-free; unit-testable in isolation."""
import re

SENTINEL = "⟦CTA⟧"
CHIPS_SENTINEL = "⟦CHIPS⟧"
VALID_TYPES = ("page", "email", "action", "inline")


def stream_visible(tokens, sentinel=SENTINEL):
    """Yield the visible portion of a token stream, never emitting the directive
    (the sentinel onward) and never flashing a PARTIAL sentinel.
    Holds back the trailing chars that could be the start of sentinel until
    they are confirmed safe, flushing the legitimate remainder at the end.
    `tokens` is any iterable of str chunks. Yields str deltas to send to the client.

    IMPORTANT: when the sentinel is found we stop YIELDING, but we still fully
    drain the input iterator. Callers feed a generator whose iteration has a side
    effect (appending every token to a `full`/`full_answer` accumulator that is
    later re-parsed for the directive's payload). If we abandoned the generator at
    the sentinel, the directive's own option/argument tokens (which come AFTER the
    sentinel) would never be pulled, never accumulated, and the re-parse would see
    an empty directive. So drain the rest before returning."""
    it = iter(tokens)
    acc = ""
    emitted = 0
    hold = len(sentinel) - 1
    for tok in it:
        acc += (tok or "")
        cut = acc.find(sentinel)
        if cut != -1:
            # full sentinel present: emit up to it, stop yielding, but DRAIN the
            # rest of the input so the caller's per-token side effects all run.
            if cut > emitted:
                yield acc[emitted:cut]
            emitted = cut
            for _ in it:
                pass
            return
        # no full sentinel yet: safe to emit everything except a possible
        # partial sentinel at the tail (the last `hold` chars)
        safe_end = max(emitted, len(acc) - hold)
        if safe_end > emitted:
            yield acc[emitted:safe_end]
            emitted = safe_end
    # stream ended with no sentinel: flush whatever remains (it was a real tail)
    if len(acc) > emitted:
        yield acc[emitted:]

# A first line that is only a bracketed label, e.g. "[ESTRO-CLEAR PRODUCT ANSWER]"
# or "**[EXECUTIVE SUMMARY]**". The model sometimes opens with one; it is never
# part of the answer. A markdown link "[name](url)" does not match.
_LABEL_LINE = re.compile(
    r"^(#{1,6}[ \t]*)?(\*\*|__|\*|_)?\[[^\[\]\n]{1,100}\](\*\*|__|\*|_)?:?$")
_LABEL_HOLD = 120   # a first line longer than this is never treated as a label


def _is_label_line(line):
    # Length is measured after leading whitespace, the same way the stream counts it.
    return len(line.lstrip()) <= _LABEL_HOLD and bool(_LABEL_LINE.match(line.strip()))


def _split_label(text):
    """If `text` opens (after blank lines) with a complete label line, return what
    follows it with its leading blank lines dropped. Otherwise None."""
    rest = text
    while True:
        line, nl, after = rest.partition("\n")
        if not nl:
            return "" if _is_label_line(line) else None
        if line.strip():
            break
        rest = after
    if not _is_label_line(line):
        return None
    while True:                         # drop blank lines, keep the body's indentation
        line, nl, rest = after.partition("\n")
        if line.strip():
            return after
        if not nl:
            return ""
        after = rest


def strip_leading_label_text(answer):
    """Drop a leading label line (and blank lines around it) from a whole answer."""
    text = answer or ""
    out = _split_label(text)
    return text if out is None else out


def strip_leading_label(tokens):
    """Stream form of strip_leading_label_text. Holds back only the opening lines,
    and only while they could still be a label. Drains the input like stream_visible."""
    it = iter(tokens)
    acc = ""
    for tok in it:
        acc += (tok or "")
        body = acc.lstrip()
        if body and body[0] not in "[#*_":
            break                       # cannot be a label: pass through
        if "\n" in body:
            # first line complete. A label: hold on through the blank lines after it.
            out = _split_label(acc)
            if out is None or out.strip():
                break
        elif len(body) > _LABEL_HOLD:
            break                       # first line too long to be a label
    else:
        out = strip_leading_label_text(acc)
        if out:
            yield out
        return
    out = strip_leading_label_text(acc)
    if out:
        yield out
    yield from it


def parse_cta(answer: str):
    text = answer or ""
    idx = text.rfind(SENTINEL)
    if idx == -1:
        return (text.strip(), None)
    directive = text[idx + len(SENTINEL):].strip()
    clean = text[:idx].rstrip()
    parts = [p.strip() for p in directive.split("|")]
    ctype = parts[0].lower() if parts else ""
    if ctype not in VALID_TYPES:
        return (clean, None)          # strip the sentinel, no cta
    target = parts[1] if len(parts) > 1 else ""
    label = parts[2] if len(parts) > 2 else ""
    return (clean, {"type": ctype, "target": target, "label": label})

def parse_chips(answer: str):
    """Extract a trailing ⟦CHIPS⟧ a | b | c directive. Returns (clean_text, chips[<=4])."""
    text = answer or ""
    idx = text.rfind(CHIPS_SENTINEL)
    if idx == -1:
        return (text.strip(), [])
    directive = text[idx + len(CHIPS_SENTINEL):]
    clean = text[:idx].rstrip()
    chips = [c.strip() for c in directive.split("|")]
    chips = [c for c in chips if c][:4]
    return (clean, chips)
