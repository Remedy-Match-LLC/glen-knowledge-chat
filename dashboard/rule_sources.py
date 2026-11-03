"""Remove the chat's own rules from an answer's Sources line.

Every chat answer ends with a one-line Sources list, and the system prompt says never to
list an instruction or a rule there. The model does it anyway. After the Wellness
Ensurance rule went live on 2 Nov 2026, three of four read-back answers listed it, as
"Wellness Ensurance retirement directive (2026-11-02)" and the like, and the CDS rule did
the same after #2143. Glen, 2026-11-02, platform tab: "yes" to removing them in code.

Only an entry on the Sources line is touched, and only one that names a rule, directive,
notice, instruction, prompt or retirement. A real source is kept as written. A Sources
line left with nothing on it is removed.

rewrite_stream holds back only a line that may be the Sources line, so the rest of the
answer streams as before.
"""
import re

_LABEL = re.compile(r"^(\s*(?:[*_]{1,2})?\s*sources?\s*(?:[*_]{1,2})?\s*:\s*(?:[*_]{1,2})?\s*)"
                    r"(.*?)(\s*)$", re.I | re.S)
_LABEL_START = re.compile(r"\s*[*_]{0,2}\s*(?:s(?:o(?:u(?:r(?:c(?:e(?:s)?)?)?)?)?)?)?"
                          r"\s*[*_]{0,2}\s*", re.I)
_RULE_WORDS = re.compile(r"\b(?:rules?|directives?|notices?|instructions?|prompt|"
                         r"standing|retire(?:d|ment)?)\b", re.I)


def _entries(body):
    """Split a Sources body on commas and semicolons outside brackets."""
    out, cur, depth = [], "", 0
    for ch in body:
        if ch in "([":
            depth += 1
        elif ch in ")]" and depth:
            depth -= 1
        if ch in ",;" and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def scrub_line(line):
    """One line of an answer, with rule entries removed if it is the Sources line.
    Returns None when the whole line should go."""
    m = _LABEL.match(line)
    if not m:
        return line
    label, body, tail = m.groups()
    entries = _entries(body)
    kept = [e for e in entries if not _RULE_WORDS.search(e)]
    if len(kept) == len(entries):
        return line
    kept = [e.strip() for e in kept if e.strip(" .*_")]
    if not kept:
        return None
    return label + ", ".join(kept) + tail


def rewrite_text(text):
    if not text:
        return text
    out = []
    for line in text.splitlines(keepends=True):
        kept = scrub_line(line)
        if kept is not None:
            out.append(kept)
    return "".join(out)


def _may_be_label(partial):
    """Could this unfinished line still turn out to be the Sources line?"""
    return bool(_LABEL_START.fullmatch(partial)) or bool(_LABEL.match(partial))


def rewrite_stream(deltas):
    """Yield the deltas with the Sources line cleaned. Drains the input fully."""
    held = ""            # the current unfinished line, while it may be the Sources line
    passing = False      # the current line is known not to be the Sources line
    for d in deltas:
        if not d:
            continue
        if passing:
            nl = d.find("\n")
            if nl < 0:
                yield d
                continue
            yield d[:nl + 1]
            passing, d = False, d[nl + 1:]
        held += d
        while True:
            nl = held.find("\n")
            if nl >= 0:
                line, held = held[:nl + 1], held[nl + 1:]
                kept = scrub_line(line)
                if kept:
                    yield kept
                continue
            if held and not _may_be_label(held):
                yield held
                held, passing = "", True
            break
    if held:
        kept = scrub_line(held)
        if kept:
            yield kept
