"""Merge duplicate infoceutical listings into one survivor each (production, 2026-10-03).

Glen, production's tab, 2026-10-03: the infoceuticals are listed twice, merge them into one
listing each under the FileMaker name ("yes"). The mapping is production's data file:

    production/05 Formulations/_catalog/2026-10-03/infoceutical-merge.json

For each row the survivor takes the FileMaker `name` and `fmp_id`, and keeps its old names
as exact `aliases` with `report_aliases: true`, as was done for ES1 (#751). It also takes
any aliases its twins carried, and their bottle_type, qbo_item_id and description where
it has none. Each retired
twin becomes `inactive: true` with `superseded_by: <survivor>`, and its `fmp_id` moves to
the survivor: product_sales.slug_map_from_products_json keeps the LAST slug per fmp_id, so
two records sharing one would split sales history by file order.

products.json does not round-trip through json.dump, so only the touched product blocks are
rewritten, and the run asserts every other product is unchanged.

    python3 scripts/merge_infoceutical_twins.py <infoceutical-merge.json>          # dry run
    python3 scripts/merge_infoceutical_twins.py <infoceutical-merge.json> --write
"""
import copy
import json
import sys
from pathlib import Path

PRODUCTS = Path(__file__).resolve().parent.parent / "data" / "products.json"
# Spec points beyond the mapping rows. ES1's row carries no fmp_id; its retired twin
# (retired in #751) holds 245, which production asked to move onto es1-lymph.
CARRY = ("bottle_type", "qbo_item_id", "description")
# Production, 2026-10-03: carry FileMaker's dosage directions only; leave other text out.
DIRECTIONS = "build up 1 drop a day to 15 drops"
# Production, 2026-10-03: FileMaker 432 reads sold_size 30 ml, as 298 and 418 do.
SET_FIELDS = {"source": {"bottle_type": "30ml"}}
EXTRA_FMP = {"es1-lymph": ("245", "es1-immune-energetic-star-infoceutical")}


def plan(products, rows):
    out = copy.deepcopy(products)
    survivors = {r["survivor"] for r in rows}
    retired = {s for r in rows for s in r["retire"]}
    assert not survivors & retired, survivors & retired
    for r in rows:
        s = r["survivor"]
        p = out[s]
        assert not p.get("inactive"), s
        old_name = p.get("name") or ""
        p["name"] = r["name"]
        if r.get("fmp_id"):
            p["fmp_id"] = str(r["fmp_id"])
        aliases = list(p.get("aliases") or [])
        # A twin's own aliases come too: the FileMaker BFA twin was the one that answered
        # to the bare code "BFA", so dropping them would leave "BFA" resolving to nothing.
        # Except a bare code: on reports a bare "BFA" stays for Rae (Glen's call, ES1 review
        # round 3), and report_aliases would resolve it. app._resolve_remedy_slug finds the
        # code by prefix instead.
        twin_aliases = [a for t in r["retire"] for a in (out[t].get("aliases") or [])
                        if " " in a.strip()]
        # A twin's vector title ("MB 5") is a spelling of the survivor too. Its name
        # already redirects through superseded_by.
        twin_aliases += [out[t].get("pinecone_title") for t in r["retire"]
                         if out[t].get("pinecone_title") and " " in out[t]["pinecone_title"].strip()
                         and out[t].get("pinecone_title") != out[t].get("name")]
        for a in [old_name] + list(r.get("aliases") or []) + twin_aliases:
            if a and a != r["name"] and a not in aliases:
                aliases.append(a)
        if aliases:
            p["aliases"] = aliases
            p["report_aliases"] = True
        for t in r["retire"]:
            q = out[t]
            assert not q.get("inactive"), t
            # Same bottle, same product. A survivor without bottle_type falls to the
            # packer's 'default' bottle and poisons the shipping quote; one without
            # qbo_item_id sends invoice lines to QuickBooks with no item. The FileMaker
            # dosing directions fill an empty description. A value the survivor already
            # has is never overwritten.
            for k in CARRY:
                if not p.get(k) and q.get(k):
                    if k == "description" and not q[k].startswith(DIRECTIONS):
                        continue                 # only Glen's dosing line, never other copy
                    p[k] = q[k]
            q["inactive"] = True
            q["superseded_by"] = s
            fid = str(q.pop("fmp_id", "") or "")
            assert not fid or fid == p.get("fmp_id"), (t, fid, p.get("fmp_id"))
    for s, fields in SET_FIELDS.items():
        if s in survivors and not out[s].get("bottle_type"):
            out[s].update(fields)
    for s, (fid, twin) in EXTRA_FMP.items():
        if s in survivors:
            assert str(out[twin].get("fmp_id") or "") == fid, twin
            out[s]["fmp_id"] = fid
            del out[twin]["fmp_id"]
    return out


def _block_bounds(lines, slug):
    start = lines.index(f'  {json.dumps(slug, ensure_ascii=False)}: {{')
    for j in range(start + 1, len(lines)):
        if lines[j] in ("  },", "  }"):
            return start, j
    raise ValueError(slug)


def _render(slug, obj, last_line):
    body = json.dumps(obj, indent=1, ensure_ascii=False).split("\n")
    body = [("  " + l if i else l) for i, l in enumerate(body)]
    body[0] = f'  {json.dumps(slug, ensure_ascii=False)}: ' + body[0]
    if last_line.endswith(","):
        body[-1] += ","
    return body


def main(argv):
    rows = json.load(open(argv[1], encoding="utf-8"))
    raw = PRODUCTS.read_text(encoding="utf-8")
    doc = json.loads(raw)
    before = doc["products"]
    after = plan(before, rows)
    touched = [s for s in after if after[s] != before[s]]
    lines = raw.split("\n")
    for s in touched:
        a, b = _block_bounds(lines, s)
        lines[a:b + 1] = _render(s, after[s], lines[b])
    new = "\n".join(lines)
    check = json.loads(new)
    expect = dict(doc, products=after)
    assert check == expect, "rewritten file does not parse to the planned catalog"
    print(f"{len(touched)} products changed: "
          f"{sum(1 for s in touched if after[s].get('inactive'))} retired, "
          f"{sum(1 for s in touched if not after[s].get('inactive'))} survivors")
    if "--write" in argv:
        tmp = PRODUCTS.with_suffix(".json.tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(PRODUCTS)                    # atomic on the same filesystem
        print("written", PRODUCTS)


if __name__ == "__main__":
    main(sys.argv)
