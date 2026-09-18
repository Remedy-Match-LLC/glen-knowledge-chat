"""Group scan findings and clinical patterns into causal-chain layers.

Glen, 2026-09-16: "Integrate energetic and clinical patterns where possible together
into layers", by "similar/related functions", where "detox cuts across multiple
organs in different systems".

Order matters and is his: FUNCTION first, location second.

  1. same function AND same system — the strongest pairing
  2. same function, any system    — what assembles detox across four systems
  3. same system, no shared function
  4. alone

A finding may serve several functions but is placed once: a causal chain has one
home for each finding.

Findings with no tissue (the MB holograms, MR matrix regulators, BFA and the abstract
ES stars, 38 of them) carry no function and stand alone. Glen, 2026-09-16:
"ungrouped for now".
"""
import collections


def group_findings(findings):
    """[{why, members}] — every finding placed exactly once, order stable."""
    used, layers = set(), []

    def take(group, why):
        group = [f for f in group if f.get("code", id(f)) not in used]
        if len(group) < 2:
            return
        for f in group:
            used.add(f.get("code", id(f)))
        layers.append({"why": why, "members": group})

    def buckets(keyfn):
        out = collections.defaultdict(list)
        for f in findings or []:
            for key in keyfn(f):
                out[key].append(f)
        return out

    for (fn, sysname), g in sorted(
            buckets(lambda f: [(x, f.get("system")) for x in f.get("functions") or ()
                               if f.get("system")]).items()):
        take(g, f"{fn} in {sysname}")
    for fn, g in sorted(buckets(lambda f: f.get("functions") or ()).items(),
                        key=lambda kv: (-len(kv[1]), kv[0])):
        take(g, f"{fn} (across systems)")
    for sysname, g in sorted(buckets(
            lambda f: [f["system"]] if f.get("system") else []).items()):
        take(g, sysname)
    for f in findings or []:
        if f.get("code", id(f)) not in used:
            used.add(f.get("code", id(f)))
            layers.append({"why": "on its own", "members": [f]})
    return layers
