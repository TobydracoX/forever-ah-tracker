#!/usr/bin/env python3
"""Hourly WoW Forever auction house collector.

Pulls the auction house snapshot from Blizzard's Game Data API, keeps only
watchlist items, and appends one row per item to data/prices/YYYY-MM.csv.
Also checks news feeds for market-moving posts (data/events.json).

Needs two environment variables (GitHub secrets):
  BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET

Standard library only, so it runs anywhere Python 3.9+ does.
"""
import base64
import concurrent.futures
import csv
import gzip
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PRICES = os.path.join(DATA, "prices")
CONFIG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
REGION = CONFIG["region"]
API = f"https://{REGION}.api.blizzard.com"
DYN = CONFIG.get("dynamic_namespace") or f"dynamic-forever-{REGION}"
STATIC = CONFIG.get("static_namespace") or f"static-forever-{REGION}"
LOCALE = CONFIG.get("locale", "en_US")
# Listings that vanish while they still had this much time left were most
# likely bought (or cancelled); short ones may simply have expired.
SOLD_TIME_LEFT = {"LONG", "VERY_LONG"}


def log(*a):
    print(*a, flush=True)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False, separators=None if indent else (",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- HTTP

def http(url, headers=None, data=None, tries=3):
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            last = f"HTTP {e.code} for {url}: {body}"
            if e.code in (400, 401, 403, 404):
                break
        except Exception as e:  # network hiccup
            last = f"{type(e).__name__} for {url}: {e}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(last)


TOKEN = None


def token():
    global TOKEN
    if TOKEN:
        return TOKEN
    cid = os.environ.get("BLIZZARD_CLIENT_ID")
    secret = os.environ.get("BLIZZARD_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Missing BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET. Add them as repository secrets.")
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body, _ = http("https://oauth.battle.net/token",
                   headers={"Authorization": f"Basic {auth}",
                            "Content-Type": "application/x-www-form-urlencoded"},
                   data=b"grant_type=client_credentials")
    TOKEN = json.loads(body)["access_token"]
    return TOKEN


def api(path, namespace, **params):
    params = {"namespace": namespace, "locale": LOCALE, **params}
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    body, headers = http(url, headers={"Authorization": f"Bearer {token()}"})
    return json.loads(body), headers


# ---------------------------------------------------------------- items

def watch_names():
    """(name, category) for every tracked item: dashboard watchlist first, then background."""
    names, seen = [], set()
    for section in ("watchlist", "background"):
        for cat, items in CONFIG.get(section, {}).items():
            for n in items:
                if n not in seen:
                    seen.add(n)
                    names.append((n, cat))
    return names


def visible_names():
    return {n for items in CONFIG["watchlist"].values() for n in items}


def resolve_items():
    """Map each watchlist name to its item ids (cached in data/items.json)."""
    path = os.path.join(DATA, "items.json")
    cache = load_json(path, {})
    changed = False
    for name, cat in watch_names():
        entry = cache.get(name)
        if entry and entry.get("ids"):
            if entry.get("category") != cat:
                entry["category"] = cat
                changed = True
            continue
        if CONFIG.get("item_ids", {}).get(name):
            cache[name] = {"ids": sorted(CONFIG["item_ids"][name]), "category": cat}
            changed = True
            log(f"  item {name!r}: {cache[name]['ids']} (from config)")
            continue
        try:
            ids = []
            page, pages = 1, 1
            while page <= min(pages, 10) and not ids:
                res, _ = api("/data/wow/search/item", STATIC,
                             **{f"name.{LOCALE}": name, "orderby": "id:desc", "_pageSize": 1000, "_page": page})
                pages = res.get("pageCount", 1) or 1
                for r in res.get("results", []):
                    d = r.get("data", {})
                    nm = (d.get("name") or {}).get(LOCALE, "")
                    if nm.lower() == name.lower():
                        ids.append(d["id"])
                page += 1
            cache[name] = {"ids": sorted(set(ids)), "category": cat}
            changed = True
            log(f"  item {name!r}: {cache[name]['ids'] or 'NOT FOUND (check spelling)'}")
        except Exception as e:
            log(f"  item {name!r}: lookup failed: {e}")
    if changed:
        save_json(path, cache, indent=1)
    return cache


# ---------------------------------------------------------------- houses

def pick_houses():
    rid = CONFIG["ruleset_id"]
    idx, _ = api(f"/data/wow/ruleset/{rid}/auctions/", DYN)
    save_json(os.path.join(DATA, "houses_raw.json"), idx, indent=1)
    try:
        detail, _ = api(f"/data/wow/ruleset/{rid}", DYN)
        save_json(os.path.join(DATA, "ruleset_raw.json"), detail, indent=1)
    except Exception as e:
        log(f"  ruleset detail: {e}")
    houses = []
    for h in idx.get("auctions", idx.get("auction_houses", [])):
        hid = h.get("id")
        name = h.get("name")
        if isinstance(name, dict):
            name = name.get(LOCALE) or next(iter(name.values()), "")
        houses.append({"id": hid, "name": name or str(hid)})
    save_json(os.path.join(DATA, "houses.json"), houses, indent=1)
    if CONFIG.get("house_ids"):
        chosen = [h for h in houses if h["id"] in CONFIG["house_ids"]]
    else:
        fac = CONFIG.get("faction", "").lower()
        chosen = [h for h in houses if fac and fac in h["name"].lower()]
    if not chosen:
        log("  No auction house matched the faction; using all houses. "
            "See data/houses.json and set house_ids in config.json to narrow it.")
        chosen = houses
    log("  houses:", ", ".join(f"{h['id']}={h['name']}" for h in chosen))
    return chosen


MARKET = {}  # item id -> [(unit_price, qty), ...] for every commodity on the house (whole-market scan)


def fetch_listings(houses, wanted_ids):
    """Return (listings, snapshot_time). listings: list of dicts for wanted items."""
    rid = CONFIG["ruleset_id"]
    MARKET.clear()
    out, stamps = [], []
    used = []
    for h in houses:
        for suffix in ("", "/commodities"):
            path = f"/data/wow/ruleset/{rid}/auctions/{h['id']}{suffix}"
            try:
                res, headers = api(path, DYN)
            except Exception as e:
                log(f"  {path}: {e}")
                continue
            if suffix == "":
                # Each house says which ruleset it belongs to; skip the other rulesets' houses.
                owner = (res.get("ruleset") or {}).get("id")
                if owner is not None and owner != rid and not CONFIG.get("house_ids"):
                    log(f"  house {h['id']} belongs to ruleset {owner}, skipping")
                    break
                used.append(h)
            lm = headers.get("Last-Modified") or headers.get("last-modified")
            if lm:
                try:
                    stamps.append(parsedate_to_datetime(lm))
                except Exception:
                    pass
            for a in res.get("auctions", []) or []:
                item = a.get("item") or {}
                iid = item.get("id")
                if suffix == "/commodities" and iid is not None and a.get("unit_price"):
                    MARKET.setdefault(iid, []).append((int(a["unit_price"]), int(a.get("quantity") or 1)))
                if iid not in wanted_ids:
                    continue
                qty = int(a.get("quantity") or 1)
                if a.get("unit_price"):
                    unit = int(a["unit_price"])
                elif a.get("buyout"):
                    unit = int(a["buyout"]) / qty
                else:
                    continue  # bid-only listing: no buyout price
                out.append({"id": f"{h['id']}:{a.get('id')}", "item": iid, "qty": qty,
                            "unit": unit, "left": a.get("time_left", "")})
    snap = max(stamps) if stamps else datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    log("  using houses:", ", ".join(str(h["id"]) for h in used) or "none")
    return out, snap, used


# ---------------------------------------------------------------- stats

def gold(c):
    """Copper -> readable text, e.g. 138000 -> '13g 80s'."""
    if c == "" or c is None:
        return ""
    c = int(round(c))
    g, s, cp = c // 10000, c % 10000 // 100, c % 100
    if g:
        return f"{g}g {s:02d}s" if s else f"{g}g"
    if s:
        return f"{s}s {cp:02d}c" if cp else f"{s}s"
    return f"{cp}c"


def weighted_quantile(pairs, q):
    """pairs: [(price, qty)] -> price at quantile q of total quantity."""
    pairs = sorted(pairs)
    total = sum(qn for _, qn in pairs)
    if not total:
        return None
    target = q * total
    run = 0
    for p, qn in pairs:
        run += qn
        if run >= target:
            return p
    return pairs[-1][0]


def summarise(listings, items, prev_state):
    by_item = {}
    for l in listings:
        by_item.setdefault(l["item"], []).append(l)
    # what vanished since last snapshot
    now_qty = {l["id"]: l["qty"] for l in listings}
    sold = {}
    for aid, (iid, qty, left) in prev_state.get("listings", {}).items():
        if aid not in now_qty:
            if left in SOLD_TIME_LEFT:
                sold[iid] = sold.get(iid, 0) + qty
        elif now_qty[aid] < qty:  # part of a stack was bought
            sold[iid] = sold.get(iid, 0) + qty - now_qty[aid]
    rows = []
    for name, entry in items.items():
        ids = set(entry.get("ids") or [])
        if not ids:
            continue
        ls = [l for i in ids for l in by_item.get(i, [])]
        pairs = [(l["unit"], l["qty"]) for l in ls]
        est = sum(sold.get(i, 0) for i in ids) if prev_state.get("ts") else ""
        if pairs:
            rows.append([name, round(min(p for p, _ in pairs)), round(weighted_quantile(pairs, 0.5)),
                         round(weighted_quantile(pairs, 0.25)), sum(q for _, q in pairs), len(ls), est])
        else:
            rows.append([name, "", "", "", 0, 0, est])
    new_state = {"listings": {l["id"]: [l["item"], l["qty"], l["left"]] for l in listings}}
    return rows, new_state


# ---------------------------------------------------------------- ladders

def save_ladders(listings, items, prev_state, snap_iso):
    """Save every item's full price ladder for this snapshot, plus depth numbers.

    One small gzipped file per snapshot in data/ladders/YYYY-MM/, written once and
    never changed. Each item has "ladder": [[unit_price_copper, quantity, listings], ...]
    sorted by price, so any depth measure can be worked out later.
    """
    id_to_name = {i: n for n, e in items.items() for i in (e.get("ids") or [])}
    prev_ids = set(prev_state.get("listings", {}))
    have_prev = bool(prev_state.get("ts"))
    per = {}
    for l in listings:
        name = id_to_name.get(l["item"])
        if not name:
            continue
        d = per.setdefault(name, {"levels": {}, "new": 0, "new_qty": 0, "left": {}})
        price = int(round(l["unit"]))
        lv = d["levels"].setdefault(price, [0, 0])
        lv[0] += l["qty"]
        lv[1] += 1
        d["left"][l["left"]] = d["left"].get(l["left"], 0) + l["qty"]
        if have_prev and l["id"] not in prev_ids:
            d["new"] += 1
            d["new_qty"] += l["qty"]
    out = {}
    for name, d in per.items():
        ladder = sorted([p, q, n] for p, (q, n) in d["levels"].items())
        lo = ladder[0][0]
        units = sum(q for _, q, _ in ladder)
        nlist = sum(n for _, _, n in ladder)
        within5 = sum(q for p, q, _ in ladder if p <= lo * 1.05)
        cost10 = sum(p * q for p, q, _ in ladder if p <= lo * 1.10)
        out[name] = {
            "ladder": ladder,
            "depth": {
                "units_within_5pct": within5,
                "units_within_10pct": sum(q for p, q, _ in ladder if p <= lo * 1.10),
                "gold_to_lift_10pct": round(cost10 / 10000, 2),
                "avg_listing_size": round(units / nlist, 1) if nlist else 0,
                "price_levels": len(ladder),
                "new_listings": d["new"] if have_prev else None,
                "new_units": d["new_qty"] if have_prev else None,
                "units_by_time_left": d["left"],
            },
        }
    folder = os.path.join(DATA, "ladders", snap_iso[:7])
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, snap_iso.replace(":", "-") + ".json.gz")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"ts": snap_iso, "items": out}, f, separators=(",", ":"))
    log(f"  ladders: {len(out)} items, {os.path.getsize(path) // 1024} KB")

    # Small copy for the dashboard: only dashboard items, price levels up to 3x the floor.
    shown = visible_names()
    dash = {}
    for name, v in out.items():
        if name not in shown:
            continue
        lo = v["ladder"][0][0]
        near = [l for l in v["ladder"] if l[0] <= lo * 3]
        far = [l for l in v["ladder"] if l[0] > lo * 3]
        dash[name] = {"l": near, "above": [sum(q for _, q, _ in far), sum(n for _, _, n in far)]}
    dfolder = os.path.join(DATA, "ladders-dash", snap_iso[:7])
    os.makedirs(dfolder, exist_ok=True)
    with gzip.open(os.path.join(dfolder, snap_iso.replace(":", "-") + ".json.gz"), "wt", encoding="utf-8") as f:
        json.dump({"ts": snap_iso, "items": dash}, f, separators=(",", ":"))


def ladder_index(days=31):
    """Dashboard ladder files from the last `days` days, oldest first, as paths from the site root."""
    root = os.path.join(DATA, "ladders-dash")
    files = []
    for month in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        for f in sorted(os.listdir(os.path.join(root, month))):
            if f.endswith(".json.gz"):
                files.append(f"data/ladders-dash/{month}/{f}")
    return files[-days * 24:]


# ---------------------------------------------------------------- whole market

def save_market(snap_iso):
    """One small file per snapshot with a summary row for every commodity on the house."""
    if not MARKET:
        return
    folder = os.path.join(DATA, "market", snap_iso[:7])
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, snap_iso.replace(":", "-") + ".csv.gz")
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "min", "median", "p25", "qty", "listings"])
        for iid in sorted(MARKET):
            pairs = MARKET[iid]
            w.writerow([iid, min(p for p, _ in pairs), round(weighted_quantile(pairs, 0.5)),
                        round(weighted_quantile(pairs, 0.25)), sum(q for _, q in pairs), len(pairs)])
    log(f"  market: {len(MARKET)} items, {os.path.getsize(path) // 1024} KB")


def name_market_items(limit=4000):
    """Look up names for item ids seen in the market scan (cached in data/market_items.json)."""
    path = os.path.join(DATA, "market_items.json")
    names = load_json(path, {})
    todo = [i for i in MARKET if str(i) not in names][:limit]
    if not todo:
        return names

    def one(iid):
        try:
            res, _ = api(f"/data/wow/item/{iid}", STATIC)
            nm = res.get("name")
            if isinstance(nm, dict):
                nm = nm.get(LOCALE) or next(iter(nm.values()), "")
            cls = ((res.get("item_class") or {}).get("name"))
            if isinstance(cls, dict):
                cls = cls.get(LOCALE)
            return iid, {"name": nm or f"Item {iid}", "class": cls or ""}
        except Exception:
            return iid, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for iid, info in ex.map(one, todo):
            if info:
                names[str(iid)] = info
    save_json(path, names, indent=None)
    log(f"  market names: {len(todo)} looked up, {len(names)} known")
    return names


def market_index(days=8):
    root = os.path.join(DATA, "market")
    files = []
    for month in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        for f in sorted(os.listdir(os.path.join(root, month))):
            if f.endswith(".csv.gz"):
                files.append(f"data/market/{month}/{f}")
    return files[-days * 24:]


# ---------------------------------------------------------------- news

def collect_news(item_names):
    path = os.path.join(DATA, "events.json")
    events = load_json(path, [])
    seen = {e.get("link") for e in events}
    kws = [k.lower() for k in CONFIG.get("news_keywords", [])]
    need = (CONFIG.get("news_require_word") or "").lower()
    added = 0
    for feed in CONFIG.get("news_feeds", []):
        try:
            body, _ = http(feed, headers={"User-Agent": "forever-ah-tracker/1.0"}, tries=2)
            root = ET.fromstring(body)
        except Exception as e:
            log(f"  news feed failed ({feed}): {e}")
            continue
        entries = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for it in entries:
            def tx(tag):
                el = it.find(tag)
                if el is None:
                    el = it.find("{http://www.w3.org/2005/Atom}" + tag)
                if el is None:
                    return ""
                return (el.text or el.get("href") or "").strip()
            title = tx("title")
            link = tx("link")
            desc = re.sub(r"<[^>]+>", " ", tx("description") or tx("summary"))
            date = tx("pubDate") or tx("updated") or tx("published")
            if not link or link in seen:
                continue
            text = f"{title} {desc}".lower()
            if need and need not in text:
                continue
            hits = [k for k in kws if k in text and k != need]
            tagged = [n for n in item_names if n.lower() in text]
            if not hits and not tagged:
                continue
            try:
                ts = parsedate_to_datetime(date).astimezone(timezone.utc).isoformat()
            except Exception:
                try:
                    ts = datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
                except Exception:
                    ts = datetime.now(timezone.utc).isoformat()
            events.append({"ts": ts, "title": title, "link": link, "source": urllib.parse.urlparse(feed).netloc,
                           "keywords": hits, "items": tagged})
            seen.add(link)
            added += 1
    events.sort(key=lambda e: e["ts"])
    save_json(path, events[-500:], indent=1)
    log(f"  news: {added} new market-relevant posts")


HEADER = ["ts", "item", "min", "median", "p25", "qty", "listings", "est_sold", "min_gold", "median_gold"]


def write_rows(path, snap_iso, rows):
    """Append one snapshot's rows to a month file, upgrading an old header if needed."""
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fresh = not os.path.exists(path)
    if not fresh:
        with open(path, newline="", encoding="utf-8") as f:
            old_rows = list(csv.reader(f))
        if old_rows and "min_gold" not in old_rows[0]:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(HEADER)
                for r in old_rows[1:]:
                    r = (r + [""] * 8)[:8]
                    w.writerow(r + [gold(float(r[2])) if r[2] else "", gold(float(r[3])) if r[3] else ""])
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(HEADER)
        for r in rows:
            w.writerow([snap_iso] + r + [gold(r[1]), gold(r[2])])


# ---------------------------------------------------------------- main

def main():
    os.makedirs(PRICES, exist_ok=True)
    log("Resolving watchlist items...")
    items = resolve_items()
    wanted = {i for e in items.values() for i in (e.get("ids") or [])}
    if not wanted:
        sys.exit("No watchlist items could be resolved. Check the API keys and static namespace.")

    log("Finding auction houses...")
    houses = pick_houses()
    log("Downloading listings...")
    listings, snap, houses = fetch_listings(houses, wanted)
    snap_iso = snap.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    state_path = os.path.join(DATA, "state.json")
    prev = load_json(state_path, {})
    if prev.get("ts") == snap_iso:
        log(f"Snapshot {snap_iso} already recorded; nothing new from Blizzard yet.")
    else:
        breakdown = {}
        id_to_name = {i: n for n, e in items.items() for i in (e.get("ids") or [])}
        for l in listings:
            hid = l["id"].split(":")[0]
            nm = id_to_name.get(l["item"], str(l["item"]))
            b = breakdown.setdefault(hid, {}).setdefault(nm, {"min": None, "qty": 0, "listings": 0})
            b["min"] = l["unit"] if b["min"] is None else min(b["min"], l["unit"])
            b["qty"] += l["qty"]
            b["listings"] += 1
        for hb in breakdown.values():
            for b in hb.values():
                b["min_gold"] = gold(b["min"])
        save_json(os.path.join(DATA, "house_breakdown.json"), {"ts": snap_iso, "houses": breakdown}, indent=1)
        try:
            save_market(snap_iso)
        except Exception as e:
            log(f"  market step failed: {e}")
        try:
            save_ladders(listings, items, prev, snap_iso)
        except Exception as e:
            log(f"  ladder step failed: {e}")
        rows, new_state = summarise(listings, items, prev)
        new_state["ts"] = snap_iso
        shown = visible_names()
        write_rows(os.path.join(PRICES, snap_iso[:7] + ".csv"), snap_iso, [r for r in rows if r[0] in shown])
        write_rows(os.path.join(DATA, "prices-bg", snap_iso[:7] + ".csv"), snap_iso, [r for r in rows if r[0] not in shown])
        save_json(state_path, new_state)
        log(f"Recorded snapshot {snap_iso}: {len(listings)} watched listings, {len(rows)} items "
            f"({len([r for r in rows if r[0] in shown])} on the dashboard).")

    try:
        if MARKET:
            name_market_items()
    except Exception as e:
        log(f"  market names step failed: {e}")
    try:
        collect_news(list(items.keys()))
    except Exception as e:
        log(f"  news step failed: {e}")

    months = sorted(f[:-4] for f in os.listdir(PRICES) if f.endswith(".csv"))
    save_json(os.path.join(DATA, "manifest.json"), {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_snapshot": load_json(state_path, {}).get("ts"),
        "region": REGION, "ruleset": CONFIG.get("ruleset_name"), "faction": CONFIG.get("faction"),
        "houses": [h["name"] for h in houses],
        "months": months,
        "ladders": ladder_index(),
        "market": market_index(),
        "categories": {cat: names for cat, names in CONFIG["watchlist"].items()},
        "recipes": CONFIG.get("recipes", {}),
        "favorites": CONFIG.get("favorites", []),
        "missing": [n for n, e in items.items() if not e.get("ids") and n in visible_names()],
        "background_items": len([n for n in items if n not in visible_names()]),
        "background_categories": {cat: [n for n in names if n not in visible_names()]
                                  for cat, names in CONFIG.get("background", {}).items()},
        "background_missing": [n for n, e in items.items() if not e.get("ids") and n not in visible_names()],
    }, indent=1)
    log("Done.")


if __name__ == "__main__":
    main()
