"""Glen's never-recommend rules hold on every reveal a client can open.

2026-09-23: #1796 lets a free member see the full reveal without Glen's first approval,
which had been the human check on these rules. Glen: "filter first".
"""
import sqlite3

from dashboard import reveal_screen as rs


def _rem(slug, name):
    return {"slug": slug, "name": name, "meaning": "m"}


def _row(*remedies):
    return {"id": 1, "remedies": [dict(r) for r in remedies],
            "layers": [{"n": i + 1, "title": f"L{i}", "summary": "s", "remedy": dict(r)}
                       for i, r in enumerate(remedies)]}


EMM = _rem("electrolyte-mineral-manna", "Electrolyte Mineral Manna")
BB = _rem("bioavailability-blend", "Bioavailability Blend")
BBP = _rem("bioavailability-blend-powder", "Bioavailability Blend Powder")
FUNGI = _rem("fungifuge", "Fungifuge")
CC = _rem("candida-cleanse", "Candida Cleanse")
MICRO = _rem("microbiome", "Microbiome")


def _slugs(row):
    return [r["slug"] for r in row["remedies"]]


def test_never_list_products_are_removed():
    out = rs.screen(_row(MICRO, EMM, BB, BBP))
    assert _slugs(out) == ["microbiome"]
    assert [L["remedy"] and L["remedy"]["slug"] for L in out["layers"]] == \
        ["microbiome", None, None, None]


def test_a_barred_layer_keeps_its_title_and_summary():
    out = rs.screen(_row(EMM))
    assert out["layers"][0]["title"] == "L0" and out["layers"][0]["summary"] == "s"


def test_fungifuge_alone_is_removed():
    assert _slugs(rs.screen(_row(MICRO, FUNGI))) == ["microbiome"]


def test_fungifuge_after_a_candida_cleanse_stays():
    assert _slugs(rs.screen(_row(CC, FUNGI))) == ["candida-cleanse", "fungifuge"]


def test_a_name_without_a_slug_is_still_caught():
    out = rs.screen(_row({"name": "Electrolyte Mineral Manna"}, MICRO))
    assert [r.get("name") for r in out["remedies"]] == ["Microbiome"]


def test_the_stored_row_is_not_mutated():
    row = _row(EMM)
    rs.screen(row)
    assert row["remedies"][0]["slug"] == "electrolyte-mineral-manna"


def test_a_clean_reveal_is_unchanged():
    row = _row(MICRO, CC)
    assert rs.screen(row) == row


def test_the_client_token_path_is_screened(monkeypatch, tmp_path):
    """Through the real app._biofield_verify_token, which every client route uses."""
    import app
    from datetime import datetime, timedelta, timezone
    from dashboard import biofield_reveals as br
    monkeypatch.setattr(app, "LOG_DB", str(tmp_path / "chat_log.db"))
    th = "a" * 64
    with sqlite3.connect(app.LOG_DB) as cx:
        br.init_table(cx)
        cx.execute("CREATE TABLE IF NOT EXISTS auth_tokens (token_hash TEXT, email TEXT, "
                   "purpose TEXT, expires_at TEXT)")
        cx.execute("INSERT INTO auth_tokens VALUES (?,?,?,?)",
                   (th, "c@example.com", "biofield_reveal",
                    (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        rid, _ = br.upsert(cx, "c@example.com", "2026-09-22", {}, [MICRO, EMM, FUNGI],
                        "test", layers=_row(MICRO, EMM, FUNGI)["layers"])
        br.set_token(cx, rid, th)
        cx.commit()
    ok, row = app._biofield_verify_token(th)
    assert ok is True
    assert _slugs(row) == ["microbiome"], "a never-list product reached a client route"
