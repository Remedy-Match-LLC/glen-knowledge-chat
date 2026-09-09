"""Every portal panel a link can point at needs a hash route, or the link dies quietly.

`#finder` fell through `applyPortalHash` and did nothing, while the panel
existed as `data-panel="finder"`. So the practitioner finder could not be
deep-linked, even though Glen's client letters name it. Nothing failed loudly:
an unknown hash just returns false and the portal opens at Home.

The invariant pinned here is the one that would have caught it: the route table
and the panels in the page have to agree.
"""

import re
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "static" / "client-portal.html"


def _routes():
    h = HTML.read_text()
    m = re.search(r"const PORTAL_HASH_ROUTES = \{(.*?)\n\};", h, re.S)
    assert m, "PORTAL_HASH_ROUTES table not found"
    body = m.group(1)
    out = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        km = re.match(r'"?([a-z-]+)"?\s*:\s*\{(.*)\}', line)
        if not km:
            continue
        opts = km.group(2)
        panel = re.search(r'panel:"([a-z-]+)"', opts)
        target = re.search(r'target:"([A-Za-z0-9_-]+)"', opts)
        out[km.group(1)] = {"panel": panel.group(1) if panel else None,
                            "target": target.group(1) if target else None}
    return out


def _declared_panels():
    return set(re.findall(r'data-panel="([a-z-]+)"', HTML.read_text()))


# ── The gap that prompted this ───────────────────────────────────────────────

def test_finder_is_reachable_by_hash():
    r = _routes()
    assert "finder" in r, (
        "#finder does nothing. The panel exists as data-panel=\"finder\" but the "
        "hash table has no key for it, so a link to the practitioner finder "
        "silently opens Home instead.")
    assert r["finder"]["panel"] == "finder", r["finder"]


def test_the_finder_route_points_at_something_that_exists():
    r = _routes()["finder"]
    assert r["target"], "the finder route has no scroll target"
    assert f'id="{r["target"]}"' in HTML.read_text(), (
        f'the finder route scrolls to id="{r["target"]}", which is not in the page')


# ── The invariant, so the next panel cannot drift the same way ───────────────

def test_every_routed_panel_exists_in_the_page():
    declared = _declared_panels()
    missing = {k: v["panel"] for k, v in _routes().items()
               if v["panel"] and v["panel"] not in declared}
    assert not missing, f"hash routes point at panels that do not exist: {missing}"
