#!/usr/bin/env python3
"""Crawl every profession recipe in WoW Forever and record what uses what.

Writes:
  data/recipes.json   - every recipe: profession, tier, skill category, crafted item, reagents
  data/reagent_uses.json - item name -> {"recipes": n, "by_profession": {...}, "used_in": [...]}

Run by hand (Actions -> "Update recipe data"); recipes rarely change.
Uses the same secrets and helpers as collect.py.
"""
import concurrent.futures
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as c  # noqa: E402

STATIC = c.STATIC


def nm(x):
    if isinstance(x, dict):
        return x.get(c.LOCALE) or next(iter(x.values()), "")
    return x or ""


def main():
    profs, _ = c.api("/data/wow/profession/index", STATIC)
    recipe_refs = []
    for p in profs.get("professions", []):
        pid, pname = p["id"], nm(p.get("name"))
        try:
            pd, _ = c.api(f"/data/wow/profession/{pid}", STATIC)
        except Exception as e:
            c.log(f"profession {pname}: {e}")
            continue
        tiers = pd.get("skill_tiers") or []
        c.log(f"{pname}: {len(tiers)} tier(s)")
        for t in tiers:
            try:
                td, _ = c.api(f"/data/wow/profession/{pid}/skill-tier/{t['id']}", STATIC)
            except Exception as e:
                c.log(f"  tier {t.get('id')}: {e}")
                continue
            for cat in td.get("categories") or []:
                for r in cat.get("recipes") or []:
                    recipe_refs.append({"id": r["id"], "profession": pname, "tier": nm(t.get("name")),
                                        "category": nm(cat.get("name")),
                                        "min_skill": td.get("minimum_skill_level"), "max_skill": td.get("maximum_skill_level")})
    c.log(f"{len(recipe_refs)} recipes to read")

    def one(ref):
        try:
            rd, _ = c.api(f"/data/wow/recipe/{ref['id']}", STATIC)
        except Exception as e:
            return {**ref, "error": str(e)}
        crafted = rd.get("crafted_item") or rd.get("alliance_crafted_item") or {}
        return {**ref, "name": nm(rd.get("name")),
                "crafted": nm(crafted.get("name")), "crafted_id": crafted.get("id"),
                "crafted_qty": (rd.get("crafted_quantity") or {}).get("value") or (rd.get("crafted_quantity") or {}).get("minimum"),
                "rank": rd.get("rank"),
                "reagents": [{"id": (x.get("reagent") or {}).get("id"), "name": nm((x.get("reagent") or {}).get("name")),
                              "qty": x.get("quantity")} for x in rd.get("reagents") or []]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        recipes = list(ex.map(one, recipe_refs))
    errors = [r for r in recipes if "error" in r]
    c.log(f"read {len(recipes) - len(errors)} recipes, {len(errors)} errors")

    uses = {}
    for r in recipes:
        for g in r.get("reagents", []):
            if not g.get("name"):
                continue
            u = uses.setdefault(g["name"], {"id": g["id"], "recipes": 0, "by_profession": {}, "used_in": []})
            u["recipes"] += 1
            u["by_profession"][r["profession"]] = u["by_profession"].get(r["profession"], 0) + 1
            u["used_in"].append({"recipe": r.get("name"), "makes": r.get("crafted"), "profession": r["profession"],
                                 "category": r.get("category"), "qty": g.get("qty")})
    c.save_json(os.path.join(c.DATA, "recipes.json"), recipes, indent=1)
    c.save_json(os.path.join(c.DATA, "reagent_uses.json"), dict(sorted(uses.items())), indent=1)
    c.log(f"{len(uses)} different reagents")


if __name__ == "__main__":
    main()
