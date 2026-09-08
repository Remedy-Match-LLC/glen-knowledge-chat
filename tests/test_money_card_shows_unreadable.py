"""A rail the API could not read must not render as $0 on the dashboard.

`dashboard/money.py` now returns None plus an error for a rail it could not
read, so a zero keeps meaning zero. That is worthless if the page turns None
back into a zero on the way to the screen, and it did:

    const fmtMoney = n => "$" + (Number(n)||0).toLocaleString(...)

`Number(null)` is 0, so a rail reporting "I do not know" rendered as "$0".
The honest API and the lying display would have cancelled out.
"""

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


def _extract():
    html = (Path(__file__).resolve().parent.parent / "static" / "dashboard.html").read_text()
    m = re.search(r"/\* === money cards \(test-extracted\) === \*/(.*?)"
                  r"/\* === end money cards === \*/", html, re.S)
    assert m, "money-card marker block not found in dashboard.html"
    return m.group(1)


def _run(body):
    script = _extract() + textwrap.dedent(body)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_an_unreadable_rail_does_not_render_as_a_zero():
    _run("""
      function assert(c, m){ if(!c){ console.error("FAIL: " + m); process.exit(1); } }
      const out = R.moneyWeek({
        pb_collected: null, pb_outstanding: null, an_net: 1234, an_count: 7,
        errors: {practice_better: "RuntimeError: 400 Client Error"}
      });
      assert(!/\\$0\\b/.test(out), "an unreadable rail rendered as $0: " + out);
      assert(/1,234/.test(out), "the healthy rail lost its figure: " + out);
      console.log("OK");
    """)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_a_genuine_zero_still_renders_as_a_zero():
    _run("""
      function assert(c, m){ if(!c){ console.error("FAIL: " + m); process.exit(1); } }
      const out = R.moneyWeek({
        pb_collected: 0, pb_outstanding: 0, an_net: 0, an_count: 0, errors: {}
      });
      assert(/\\$0/.test(out), "a genuine zero week stopped rendering as $0: " + out);
      console.log("OK");
    """)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_today_card_marks_the_broken_rail_too():
    _run("""
      function assert(c, m){ if(!c){ console.error("FAIL: " + m); process.exit(1); } }
      const out = R.moneyToday({
        pb_today: 50, an_today: null, wise_balances: [],
        errors: {authorize_net: "RuntimeError: E00007"}
      });
      assert(!/Authnet<\\/span><span class="figure sm">\\$0/.test(out),
        "the broken Authnet rail rendered as $0: " + out);
      console.log("OK");
    """)
