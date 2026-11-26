#!/usr/bin/env python3
"""One-off check: lowest price and units for a few items in every auction house of every ruleset.

Writes data/house_compare.json. Run by hand (Actions -> "Compare auction houses").
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as c  # noqa: E402

ITEMS = ["Black Lotus", "Arcanite Bar", "Thorium Bar", "Rugged Leather", "Peacebloom", "Dreamfoil", "Runecloth"]


def main():
    items = c.load_json(os.path.join(c.DATA, "items.json"), {})
    ids = {i: n for n in ITEMS for i in (items.get(n, {}).get("ids") or [])}
    rulesets = {1: "PvP", 2: "Normal", 3: "Roleplay", 15: "Roleplay PvP"}
    out = []
    for rid, rname in rulesets.items():
        try:
            idx, _ = c.api(f"/data/wow/ruleset/{rid}/auctions/", c.DYN)
        except Exception as e:
            c.log(f"ruleset {rid}: {e}")
            continue
        for h in idx.get("auctions", []):
            hid, hname = h.get("id"), c.LOCALE and (h.get("name") if isinstance(h.get("name"), str) else (h.get("name") or {}).get(c.LOCALE))
            try:
                detail, _ = c.api(f"/data/wow/ruleset/{rid}/auctions/{hid}", c.DYN)
                com, _ = c.api(f"/data/wow/ruleset/{rid}/auctions/{hid}/commodities", c.DYN)
            except Exception as e:
                c.log(f"  house {hid}: {e}")
                continue
            owner = (detail.get("ruleset") or {}).get("id")
            if owner is not None and owner != rid:
                continue  # each house is listed under every ruleset; keep it under its own
            per = {}
            for a in (com.get("auctions") or []) + (detail.get("auctions") or []):
                iid = (a.get("item") or {}).get("id")
                if iid not in ids:
                    continue
                q = int(a.get("quantity") or 1)
                unit = a.get("unit_price") or ((a.get("buyout") or 0) / q if a.get("buyout") else None)
                if not unit:
                    continue
                p = per.setdefault(ids[iid], {"min": None, "qty": 0})
                p["min"] = unit if p["min"] is None else min(p["min"], unit)
                p["qty"] += q
            for v in per.values():
                v["min_gold"] = c.gold(v["min"])
            out.append({"ruleset": rname, "ruleset_id": rid, "house": hid, "name": hname, "owner_ruleset": owner, "items": per})
            c.log(f"{rname:13} {hid} {hname}: " + ", ".join(f"{k} {v['min_gold']}" for k, v in per.items()))
    c.save_json(os.path.join(c.DATA, "house_compare.json"), out, indent=1)


if __name__ == "__main__":
    main()
