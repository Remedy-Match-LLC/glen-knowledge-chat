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


def test_no_old_sentence_is_left_and_29_new_ones_are_there():
    new_total = 0
    for name in ("products.json", "products-manual-corrections.json"):
        p = _load(name)
        for slug, (old, new) in PAIRS.items():
            rec = p.get(slug) or {}
            for f in FIELDS:
                v = rec.get(f) or ""
                assert old not in v, (name, slug, f)
                new_total += v.count(new)
    assert new_total == 29


def test_b17_max_biofield_timing_ends_or_as_guided():
    src = (ROOT / "dashboard" / "biofield_authoring.py").read_text()
    assert '"timing": "with cooked food, or as guided. " + _B17_MAX_STEPS' in src
