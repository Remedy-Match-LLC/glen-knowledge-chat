"""The "or as guided" prose copies. Glen, platform tab, 2026-11-26: "yes, apply the
or-as-guided prose copies". Spec: production/05 Formulations/_store-updates/
2026-11-26-or-as-guided-review/prose-copies.json (sha256 f1b73ab7fc61...)."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIELDS = ("description", "intro", "learn_markdown")
PAIRS = {
    "soothe-powder": ("1 scoop (500 mg) 2 times a day in drinks.",
                      "1 scoop (500 mg) 2 times a day in drinks, or as guided."),
    "vitamin-a-syntropy": ("Take 1 capsule daily with food, as guided.",
                           "Take 1 capsule daily with food, or as guided."),
    "vitamin-c-syntropy": ("Take 1 capsule daily before food, as guided.",
                           "Take 1 capsule daily before food, or as guided."),
    "curcu-guard": ("Take 1 capsule up to 3 times a day with food.",
                    "Take 1 capsule up to 3 times a day with food, or as guided."),
    "moisture-eyes-night-oil": ("Shake well, then apply topically around eyes.",
                                "Shake well, then apply topically around eyes, or as guided."),
    "ocuheal-eye-drops": ("Use 1 drop in each eye 2 times a day.",
                          "Use 1 drop in each eye 2 times a day, or as guided."),
    "ocuheal-plus-eye-drops": ("Use 1 drop in each eye 2 times a day.",
                               "Use 1 drop in each eye 2 times a day, or as guided."),
    "reverse-age": ("Take 1 to 3 capsules a day with food, as guided.",
                    "Take 1 to 3 capsules a day with food, or as guided."),
}


def _load(name):
    d = json.loads((ROOT / "data" / name).read_text())
    return d.get("products", d)


def _main(name):
    import subprocess
    import pytest
    try:
        raw = subprocess.run(["git", "show", f"origin/main:data/{name}"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except Exception:
        pytest.skip("origin/main not available")
    d = json.loads(raw)
    return d.get("products", d)


EXPECTED = {"products.json": 20, "products-manual-corrections.json": 9}


def test_each_copy_is_exactly_the_approved_replacement():
    # Per file, slug and field (review round 2): every changed field equals its old
    # text with only its own sentence replaced, and the totals are 20 and 9.
    for name, want in EXPECTED.items():
        old, new = _main(name), _load(name)
        if all(o not in (old.get(s) or {}).get(f, "") for s, (o, _n) in PAIRS.items()
               for f in FIELDS):
            continue   # already on main: nothing left to compare
        got = 0
        for slug, (o, n) in PAIRS.items():
            for f in FIELDS:
                before = (old.get(slug) or {}).get(f) or ""
                after = (new.get(slug) or {}).get(f) or ""
                assert after == before.replace(o, n), (name, slug, f)
                assert ", or as guided, or as guided" not in after and "or or as" not in after
                got += before.count(o)
        assert got == want, (name, got)


def test_no_old_sentence_is_left():
    for name in EXPECTED:
        p = _load(name)
        for slug, (old, _new) in PAIRS.items():
            for f in FIELDS:
                assert old not in ((p.get(slug) or {}).get(f) or ""), (name, slug, f)


def test_b17_max_biofield_timing_ends_or_as_guided():
    src = (ROOT / "dashboard" / "biofield_authoring.py").read_text()
    assert '"timing": "with cooked food, or as guided. " + _B17_MAX_STEPS' in src
