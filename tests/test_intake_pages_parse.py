"""Every script block the Biofield Intake pages serve must actually parse.

2026-09-03: the author page carried an unterminated JS string. A Python `\\n\\n`
inside the embedded JS rendered as REAL newlines inside a single-quoted literal,
so the whole 36KB script block failed to parse and every handler defined in it
went undefined at once. The page looked normal and did nothing: the client-name
autocomplete never opened, "Save header" saved nothing, and the E4L check could
not run. Glen lost an intake to it.

Nothing else caught this. The existing tests assert that "nameSearch" appears in
the HTML, and it did appear -- inside a script the browser threw away.
"""
import re
import shutil
import subprocess
import tempfile

import pytest

from dashboard.biofield_report_html import (render_author_html, render_list_html,
                                            render_report_html)

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")

REP = {"test_id": "a1", "client": {"name": "Judith Tom", "email": "j@example.com"},
       "date": "2026-09-03", "layers": [], "schedule": {"slots": [], "entries": []}}


def _scripts(html):
    return [b for b in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S)
            if b.strip()]


def _assert_parses(html, label, *, expect_scripts=True):
    blocks = _scripts(html)
    if expect_scripts:
        assert blocks, f"{label}: no script blocks found; the extractor is broken"
    for i, block in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(block)
            path = f.name
        r = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, (
            f"{label}: script block {i} does not parse:\n{r.stderr[:800]}")


def test_author_page_javascript_parses():
    _assert_parses(render_author_html(REP), "author page")


def test_author_page_javascript_parses_for_an_empty_intake():
    """The state Glen was in: a brand-new intake with no client yet."""
    empty = {"test_id": "a35", "client": {"name": "", "email": ""},
             "date": "", "layers": [], "schedule": {"slots": [], "entries": []}}
    _assert_parses(render_author_html(empty), "author page (empty intake)")


def test_list_page_javascript_parses():
    # The list page carries no inline script today; this guards a future one.
    _assert_parses(render_list_html([], "", []), "list page", expect_scripts=False)
