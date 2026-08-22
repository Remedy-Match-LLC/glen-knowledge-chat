import json
from pathlib import Path
import begin_funnel
import shell_nav

CFG = Path(__file__).resolve().parent.parent / "static" / "shell-map.json"


def _land_keys():
    return [s["key"] for s in begin_funnel.JOURNEY_STEPS]


def test_shipped_config_is_valid():
    cfg = json.loads(CFG.read_text())
    assert shell_nav.validate_shell_map(cfg, _land_keys()) == []


def test_validator_flags_unknown_land():
    bad = {"lands": {"scan": {"name": "x", "category": "scan", "intrigue": "y"},
                     "BOGUS": {"name": "z", "category": "scan", "intrigue": "y"}},
           "categories": {"scan": {"icon": "🌀"}}}
    errs = shell_nav.validate_shell_map(bad, ["scan", "find", "heal", "give"])
    assert any("BOGUS" in e for e in errs)


def test_validator_flags_missing_category_style():
    bad = {"lands": {"scan": {"name": "x", "category": "missing", "intrigue": "y"}},
           "categories": {}}
    errs = shell_nav.validate_shell_map(bad, ["scan", "find", "heal", "give"])
    assert any("missing" in e for e in errs)


def test_featured_slugs_exist_in_catalog():
    import json
    from dashboard import products as _products
    cfg = json.loads(CFG.read_text())
    catalog = set(_products.load_products().keys())
    missing = []
    for key, land in cfg["lands"].items():
        f = land.get("featured")
        if f and f["product_slug"] not in catalog:
            missing.append(f["product_slug"])
    assert missing == [], f"featured slugs not in products.json: {missing}"


def test_trademark_names_present():
    cfg = json.loads(CFG.read_text())
    names = {k: v["name"] for k, v in cfg["lands"].items()}
    assert names["scan"] == "Wellness Whispering"
    assert names["find"] == "Remedy Match"
    assert names["heal"] == "Accelerated Self Healing™"  # ™
    assert names["give"] == "Healing Oasis"


def test_header_has_short_desktop_and_phone_labels():
    cfg = json.loads(CFG.read_text())
    assert [cfg["lands"][key]["nav_name"] for key in _land_keys()] == [
        "Wellness", "Remedies", "Healing", "Oasis"
    ]
    assert [cfg["lands"][key]["mobile_name"] for key in _land_keys()] == [
        "Scan", "Match", "Heal", "Oasis"
    ]


def test_header_controls_are_text_only_and_use_responsive_labels():
    shell = (CFG.parent / "shell.js").read_text()
    assert '"Home"' in shell
    assert '"Back"' in shell
    assert '"Map"' in shell
    assert 'js-label-desktop' in shell
    assert 'js-label-mobile' in shell
    for icon in ("🏠", "←", "🗺️", "🛒", "💎"):
        assert icon not in shell


def test_scene_block_image_and_thumbs_exist_on_disk():
    base = CFG.parent  # static/
    cfg = json.loads(CFG.read_text())
    img = cfg["scene"]["image"].lstrip("/").split("static/", 1)[-1]
    assert (base / img).exists(), f"scene image missing: {img}"
    for k, land in cfg["lands"].items():
        rel = land["thumb"].lstrip("/").split("static/", 1)[-1]
        assert (base / rel).exists(), f"thumb missing for {k}: {land['thumb']}"


def test_scene_hotspots_numeric_for_home_and_four_lands():
    cfg = json.loads(CFG.read_text())
    hs = cfg["scene"]["hotspots"]
    for key in ["home", "scan", "find", "heal", "give"]:
        spot = hs[key]
        for f in ("x", "y", "w", "h"):
            assert isinstance(spot[f], (int, float)), f"{key}.{f} not numeric"


def test_validator_flags_bad_scene():
    bad = {"lands": {"scan": {"name": "x", "category": "scan", "intrigue": "y", "thumb": "/t.webp"}},
           "categories": {"scan": {"icon": "🌀"}},
           "scene": {"image": "", "order": [], "hotspots": {}}}
    errs = shell_nav.validate_shell_map(bad, ["scan", "find", "heal", "give"])
    assert any("scene.image" in e for e in errs)
    assert any("hotspot" in e for e in errs)
