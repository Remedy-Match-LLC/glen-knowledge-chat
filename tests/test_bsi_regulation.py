"""Regulation level from the four BSI numbers (live session segments, plan 2026-11-07).

Glen's table, confirmed 7 November 2026, checked top down:
  5 Optimum   C = 1 and D = 1
  1 Blocked   C = A and D = B
  3 Mixed     (C > A and D < B) or (C < A and D > B)
  2 Negative  C >= A and D >= B, not both equal
  4 Positive  C <= A and D <= B, not both equal
"""
import itertools
import json
import re
import shutil
import subprocess

import pytest

from dashboard.terrain_phase import (bsi_num, bsi_warnings, regulation_display,
                                     regulation_level)

RANGE = range(1, 22)


@pytest.mark.parametrize("a,b,c,d,level", [
    (13, 8, 1, 1, 5),     # Optimum
    (1, 1, 1, 1, 5),      # Optimum is checked before Blocked
    (13, 8, 13, 8, 1),    # Blocked: no change
    (13, 8, 15, 5, 3),    # Mixed: C up, D down
    (13, 8, 10, 9, 3),    # Mixed: C down, D up
    (13, 8, 15, 10, 2),   # Negative: both up
    (13, 8, 13, 10, 2),   # Negative: C equal, D up
    (13, 8, 15, 8, 2),    # Negative: C up, D equal
    (13, 8, 5, 3, 4),     # Positive: both down
    (13, 8, 13, 3, 4),    # Positive: C equal, D down
    (13, 8, 5, 8, 4),     # Positive: C down, D equal
    (21, 1, 1, 1, 5),     # the transcript example in test_biofield_interpret.py
    (2, 1, 1, 2, 3),      # Mixed with D > C: computed, never rejected
    (3, 5, 3, 5, 1),      # Blocked with B > A: computed, never rejected
])
def test_hand_cases(a, b, c, d, level):
    r = regulation_level(a, b, c, d)
    assert r is not None and r[0] == level, (a, b, c, d, r)


def test_names():
    assert [regulation_level(*x)[1] for x in
            [(9, 9, 9, 9), (5, 5, 7, 7), (9, 5, 12, 3), (9, 5, 4, 2), (9, 5, 1, 1)]] == \
        ["Blocked", "Negative", "Mixed", "Positive", "Optimum"]


def test_every_valid_combination_gets_a_level_by_the_first_matching_rule():
    """All 21^4 = 194,481 readings, B > A and D > C included. Each must get a level,
    and it must be the level of the FIRST rule in the table that matches."""
    rules = [
        (5, lambda a, b, c, d: c == 1 and d == 1),
        (1, lambda a, b, c, d: c == a and d == b),
        (3, lambda a, b, c, d: (c > a and d < b) or (c < a and d > b)),
        (2, lambda a, b, c, d: c >= a and d >= b and not (c == a and d == b)),
        (4, lambda a, b, c, d: c <= a and d <= b and not (c == a and d == b)),
    ]
    seen = set()
    n = 0
    for a, b, c, d in itertools.product(RANGE, RANGE, RANGE, RANGE):
        r = regulation_level(a, b, c, d)
        assert r is not None, (a, b, c, d)
        first = next(lvl for lvl, rule in rules if rule(a, b, c, d))
        assert r[0] == first, (a, b, c, d, r)
        seen.add(r[0])
        n += 1
    assert n == 21 ** 4
    assert seen == {1, 2, 3, 4, 5}


@pytest.mark.parametrize("bad", [None, "", 0, 22, -1, "x", 3.5, True, [1]])
def test_missing_or_out_of_range_gives_none(bad):
    for i in range(4):
        args = [10, 5, 8, 4]
        args[i] = bad
        assert regulation_level(*args) is None, args


def test_bsi_num_accepts_numeric_strings_and_whole_floats():
    assert bsi_num("7") == 7 and bsi_num(" 21 ") == 21 and bsi_num(4.0) == 4
    assert bsi_num("0") is None and bsi_num("22") is None


def test_warnings_are_quiet_and_never_block():
    assert bsi_warnings(13, 8, 5, 3) == []
    assert bsi_warnings(3, 5, 5, 3) == ["B is greater than A"]
    assert bsi_warnings(5, 3, 3, 5) == ["D is greater than C"]
    assert bsi_warnings(3, 5, 3, 5) == ["B is greater than A", "D is greater than C"]
    assert regulation_level(3, 5, 3, 5) == (1, "Blocked")


def test_display():
    assert regulation_display(13, 8, 5, 3) == "Level 4 · Positive"
    assert regulation_display(13, 8, 5, None) == "Enter all four numbers"


def _js_function():
    from dashboard.biofield_report_html import _AUTHOR_JS
    m = re.search(r"// regulationLevel:begin\n(.*?)// regulationLevel:end", _AUTHOR_JS, re.S)
    assert m, "regulationLevel markers missing from _AUTHOR_JS"
    return m.group(1)


def test_page_js_agrees_with_python_over_a_grid():
    """The page recomputes Regulation live in JS. Run that JS under node over a grid and
    compare with the Python. The grid covers 1, 2, a middle value, 20 and 21 on every
    axis plus equal neighbours, and the out-of-range and blank cases."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed on this machine, so the page JS cannot be run")
    vals = [1, 2, 3, 10, 11, 12, 20, 21]
    grid = [list(x) for x in itertools.product(vals, vals, vals, vals)]
    grid += [[0, 5, 5, 5], [5, 22, 5, 5], ["", 5, 5, 5], [5, 5, None, 5], ["7", "3", "7", "3"]]
    script = (_js_function()
              + "\nconst grid=" + json.dumps(grid) + ";\n"
              + "process.stdout.write(JSON.stringify(grid.map(g=>regulationLevel(...g))));\n")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    want = [list(r) if (r := regulation_level(*g)) else None for g in grid]
    assert len(got) == len(grid)
    mismatches = [(g, j, p) for g, j, p in zip(grid, got, want) if j != p]
    assert not mismatches, mismatches[:10]


def test_mutating_the_js_is_caught():
    """The node comparison must be able to fail: swap two levels in the JS and expect
    at least one disagreement."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed on this machine, so the page JS cannot be run")
    js = _js_function().replace("l=2;", "l=__TMP__;").replace("l=4;", "l=2;") \
                       .replace("l=__TMP__;", "l=4;")
    grid = [[13, 8, 5, 3], [13, 8, 15, 10]]
    script = (js + "\nprocess.stdout.write(JSON.stringify(" + json.dumps(grid)
              + ".map(g=>regulationLevel(...g))));\n")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    got = json.loads(out.stdout)
    assert [g[0] for g in got] == [2, 4]   # swapped, so it disagrees with Python's [4, 2]
