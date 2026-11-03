"""Remove the chat's own rules from an answer's Sources line.

Every chat answer ends with a one-line Sources list, and the system prompt says never to
list an instruction or a rule there. The model does it anyway. After the Wellness
Ensurance rule went live on 2 Nov 2026, three of four read-back answers listed it, as
"Wellness Ensurance retirement directive (2026-11-02)" and the like, and the CDS rule did
the same after #2143. Glen, 2026-11-02, platform tab: "yes" to removing them in code.

Only an entry on the Sources line is touched, and only one that names both a rule word
(rule, directive, notice, instruction, prompt, standing, retired) and the subject of one
of the chat's own standing rules: Wellness Ensurance, CDS, or the chat's own prompt. So
"FDA safety notice" or "Manufacturer instructions" stays (review round 2). A real source
is kept as written. A Sources line left with nothing on it is removed.

rewrite_stream holds back only a line that may be the Sources line, so the rest of the
answer streams as before.
"""
import re

# Plural only: the chat's list is "Sources:". A study quote's single "Source:" line is
# Glen's citation format and is never touched (review round 1).
# A bullet or quote mark may lead it, and bold may close it (review round 3).
_LABEL = re.compile(r"^(\s*(?:[->]\s*)?(?:[*_]{1,2})?\s*sources\s*(?:[*_]{1,2})?\s*:\s*"
                    r"(?:[*_]{1,2})?\s*)([^\n]*?)([*_]{0,2}\s*)$", re.I)
_LABEL_START = re.compile(r"\s*(?:[->]\s*)?[*_]{0,2}\s*"
                          r"(?:s(?:o(?:u(?:r(?:c(?:e(?:s)?)?)?)?)?)?)?\s*[*_]{0,2}\s*", re.I)
# Not after a hyphen: "long-standing" or "self-instruction" is not a rule word.
_RULE_WORDS = re.compile(r"(?<![\w-])(?:rules?|directives?|notices?|instructions?|prompt|"
                         r"standing|retire(?:d|ment)?)\b", re.I)
# The subjects of the chat's standing rules (app._STANDING_USE_RULE) and its own prompt.
_RULE_SUBJECTS = re.compile(r"wellness[\s\-_]*(?:e|i)nsurance|\bcds\b|\bmms\b|"
                            r"chlorine dioxide|water purifier|system prompt|this prompt|"
                            r"\bchat(?:bot)?\b", re.I)
_LINK_TARGET = re.compile(r"\]\([^)]*\)|https?://\S+")


def _is_rule(entry):
    """A rule word and a rule subject, read from the words a reader sees: a link's
    address is ignored ("notice-of-privacy" in a URL is not a notice)."""
    seen = _LINK_TARGET.sub("]", entry)
    return bool(_RULE_WORDS.search(seen) and _RULE_SUBJECTS.search(seen))


def _entries(body):
    """Split a Sources body on commas, semicolons and bars outside brackets."""
    out, cur, depth = [], "", 0
    for ch in body:
        if ch in "([":
            depth += 1
        elif ch in ")]" and depth:
            depth -= 1
        if ch in ",;|" and depth == 0:
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
    kept = [e for e in entries if not _is_rule(e)]
    if len(kept) == len(entries):
        return line
    kept = [e.strip() for e in kept if e.strip(" .*_")]
    # A closing mark the rule entry carried ("rule**") belongs to the whole line.
    if not kept:
        return None
    return label + ", ".join(kept) + tail


def rewrite_text(text):
    if not text:
        return text
    out = []
    # Split on "\n" only, exactly as rewrite_stream does (review round 2).
    for line in re.findall(r"[^\n]*\n|[^\n]+", text):
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
