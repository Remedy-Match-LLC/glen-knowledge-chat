"""The Orders board offered Combine for the first suggested cluster only. Every other
cluster showed as "(+N more)" with no button, so a household further down the list
(Sharon and Hershey Connour, 2026-09-19) could not be combined from the banner.

Runs the page's own renderBanner() in node against a fake banner element.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parent.parent / "static" / "console-orders.html"


def _fn(src, name):
    m = re.search(r"function " + name + r"\(.*?\n  \}\n", src, re.S)
    if not m:  # one-line helpers like esc()
        m = re.search(r"function " + name + r"\(.*?\n", src)
    assert m, name
    return m.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_every_cluster_gets_its_own_combine_button():
    src = PAGE.read_text()
    clusters = [
        {"key_type": "addr", "orders": [{"id": 184, "name": "Ashley King"}, {"id": 97, "name": "Ashley King"}]},
        {"key_type": "household", "orders": [{"id": 183, "name": "Steve Fox"}, {"id": 182, "name": "Michael Hill"}]},
        {"key_type": "addr", "orders": [{"id": 193, "name": "Sharon Connour"}, {"id": 192, "name": "Hershey Connour"}]},
    ]
    script = (
        "var el={style:{},innerHTML:''};"
        "var document={getElementById:function(){return el;}};"
        "var HH={enabled:true,clusters:" + json.dumps(clusters) + "};"
        + _fn(src, "esc") + _fn(src, "renderBanner")
        + "renderBanner(); process.stdout.write(el.innerHTML);"
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    html = r.stdout
    assert re.findall(r"combineCluster\((\d+)\)", html) == ["0", "1", "2"]
    assert "Sharon Connour + Hershey Connour" in html
    assert "more)" not in html
