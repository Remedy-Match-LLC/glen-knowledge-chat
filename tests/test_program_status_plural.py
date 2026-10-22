"""The Full and Minimum program status line counts remedies. Glen, 2026-10-22: it
read "remedyies" for every count but one."""
import json
import re
import subprocess

from dashboard.biofield_report_html import _AUTHOR_JS


def _status(n):
    line = re.search(r"s\.textContent=' '\+mode\+': '\+n\+[^\n]*\n[^\n]*;", _AUTHOR_JS).group(0)
    js = ("var s={},mode='Full',j={transcript_layers:0},n=%d;%s"
          "console.log(JSON.stringify(s.textContent));" % (n, line))
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_counts_read_as_english():
    assert _status(0) == " Full: 0 remedies"
    assert _status(1) == " Full: 1 remedy"
    assert _status(2) == " Full: 2 remedies"
