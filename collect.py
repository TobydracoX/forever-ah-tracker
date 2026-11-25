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
import csv
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
    names = []
    for cat, items in CONFIG["watchlist"].items():
        for n in items:
            names.append((n, cat))
    return names


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
        try:
            res, _ = api("/data/wow/search/item", STATIC,
                         **{f"name.{LOCALE}": name, "orderby": "id", "_pageSize": 100})
            ids = []
            for r in res.get("results", []):
                d = r.get("data", {})
                nm = (d.get("name") or {}).get(LOCALE, "")
                if nm.lower() == name.lower():
                    ids.append(d["id"])
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


def fetch_listings(houses, wanted_ids):
    """Return (listings, snapshot_time). listings: list of dicts for wanted items."""
    rid = CONFIG["ruleset_id"]
    out, stamps = [], []
    for h in houses:
        for suffix in ("/commodities", ""):
            path = f"/data/wow/ruleset/{rid}/auctions/{h['id']}{suffix}"
            try:
                res, headers = api(path, DYN)
            except Exception as e:
                log(f"  {path}: {e}")
                continue
            lm = headers.get("Last-Modified") or headers.get("last-modified")
            if lm:
                try:
                    stamps.append(parsedate_to_datetime(lm))
                except Exception:
                    pass
            for a in res.get("auctions", []) or []:
                item = a.get("item") or {}
                iid = item.get("id")
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
    return out, snap


# ---------------------------------------------------------------- stats

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
            hits = [k for k in kws if k in text]
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
    listings, snap = fetch_listings(houses, wanted)
    snap_iso = snap.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    state_path = os.path.join(DATA, "state.json")
    prev = load_json(state_path, {})
    if prev.get("ts") == snap_iso:
        log(f"Snapshot {snap_iso} already recorded; nothing new from Blizzard yet.")
    else:
        rows, new_state = summarise(listings, items, prev)
        new_state["ts"] = snap_iso
        month_file = os.path.join(PRICES, snap_iso[:7] + ".csv")
        fresh = not os.path.exists(month_file)
        with open(month_file, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if fresh:
                w.writerow(["ts", "item", "min", "median", "p25", "qty", "listings", "est_sold"])
            for r in rows:
                w.writerow([snap_iso] + r)
        save_json(state_path, new_state)
        log(f"Recorded snapshot {snap_iso}: {len(listings)} watched listings, {len(rows)} items.")

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
        "categories": {cat: names for cat, names in CONFIG["watchlist"].items()},
        "recipes": CONFIG.get("recipes", {}),
        "missing": [n for n, e in items.items() if not e.get("ids")],
    }, indent=1)
    log("Done.")


if __name__ == "__main__":
    main()
