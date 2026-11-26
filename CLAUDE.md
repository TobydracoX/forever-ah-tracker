# CLAUDE.md: WoW Forever auction house tracker

> **Claude: read this whole file before answering questions about this repo or the market,
> and before changing anything.** When you change the code, config, data layout or how a
> metric is calculated, update the relevant section here **and** add a line to the
> Changelog at the bottom in the same commit. Keep it short and accurate; this file is how
> the next chat understands the project.

## What this is
A personal price tracker for **World of Warcraft: Forever** (Blizzard's level-60 game,
launched 4 Nov 2026; first raids, Onyxia's Lair, Hyjal Summit and the Barrow Deeps, open
9 Dec 2026). Every ~30 minutes it pulls the auction house from Blizzard's API, stores
prices, supply and full order books, and publishes a dashboard.

- Repo: `TobydracoX/forever-ah-tracker` (public)
- Dashboard: https://tobydracox.github.io/forever-ah-tracker/ (GitHub Pages, `main`, root)
- Market tracked: **US · Normal (PvE) ruleset · Alliance** auction house **#111**

## Owner context
- Plays Alliance on US Normal. Level ~36 (Nov 25). Skinning now; dropping Leatherworking
  for Herbalism, planning Alchemy later. Interested in raid consumables, scarce materials,
  and supply/volume-based trading.
- Prefers small, checkable steps; wants data kept forever; dashboard kept uncluttered
  (extra items tracked in the background, not shown).
- Timezone: America/Halifax (UTC−4). Talk to them in local time.

## How it runs
1. **Trigger:** cron-job.org POSTs GitHub's workflow_dispatch API at **:23 and :53** every
   hour (`https://api.github.com/repos/TobydracoX/forever-ah-tracker/actions/workflows/collect.yml/dispatches`,
   body `{"ref":"main"}`, header `Authorization: Bearer <fine-grained PAT, Actions: read+write,
   this repo only>`). GitHub's own `schedule` in the workflow never fired for this repo, so
   cron-job.org is the real scheduler. **The PAT expires** (owner chose the max length);
   when runs stop with 401, it needs regenerating and pasting into cron-job.org.
2. **Workflow:** `.github/workflows/collect.yml` runs `python collect.py` (standard library
   only) with secrets `BLIZZARD_CLIENT_ID` / `BLIZZARD_CLIENT_SECRET`, then commits `data/`
   as "price-bot" with message `Prices <UTC time>`.
3. **Snapshots:** Blizzard publishes a new auction snapshot about once an hour, usually
   around **:26 UTC**, and it arrives ~1 hour stale. Each snapshot is stamped with
   Blizzard's `Last-Modified` time; if it was already recorded, the run saves nothing
   (so extra runs are harmless). Expect one new snapshot per hour, normally picked up by
   the :53 run.

## Blizzard API facts (Forever is undocumented; these were found by players and verified here)
- Namespaces: `dynamic-forever-us`, `static-forever-us` (also `profile-forever-us`).
- No realms; **rulesets** instead: 1 PvP, 2 Normal, 3 Roleplay, 15 RP-PvP.
- `/data/wow/ruleset/{id}/auctions/` (trailing slash required) lists the same 15 houses for
  every ruleset: five each named "Alliance Auction House", "Horde Auction House",
  "Blackwater Auction House". Each house belongs to one ruleset. **US Normal Alliance = 111**
  (confirmed by outof.games and by listing counts). Houses 110/119/123 are other rulesets'
  Alliance houses; 112 was empty.
- Per house: `/auctions/{house}` (non-commodity items, response includes `ruleset`) and
  `/auctions/{house}/commodities` (stackables: herbs, potions, etc., with `unit_price`).
- Item lookup: `/data/wow/search/item?name.en_US=…` in the static namespace (paginated;
  common words need several pages). Known IDs can be pinned in `config.json` → `item_ids`.

## Files
| Path | What |
|---|---|
| `collect.py` | The collector. Resolves item names → IDs, picks houses, downloads listings, writes everything below. |
| `config.json` | Settings: `region`, `ruleset_id` (2), `faction`, `house_ids` ([111]), namespaces, `item_ids` (pinned IDs), `watchlist` (shown on dashboard), `background` (collected, not shown), `recipes` (craft-or-buy, currently empty), `favorites` (optional default stars), news feeds/keywords. |
| `index.html` | The whole dashboard (single file, Chart.js from jsDelivr). |
| `notes.json` | Owner's own events `{date, title, items[], link?}`; shown as chart lines. |
| `data/prices/YYYY-MM.csv` | Hourly rows for **dashboard** items. |
| `data/prices-bg/YYYY-MM.csv` | Same format, **background** items. |
| `data/ladders/YYYY-MM/<ts>.json.gz` | Full order book for **every** tracked item, one file per snapshot (never rewritten). |
| `data/ladders-dash/YYYY-MM/<ts>.json.gz` | Small copy for the dashboard: dashboard items only, price levels ≤ 3× floor. |
| `data/manifest.json` | What the page loads: months, categories, background_categories, ladder file list (last 31 days), missing names, last snapshot. |
| `data/items.json` | Name → item IDs cache. |
| `data/state.json` | Last snapshot's listing IDs/qty/time_left (for "est. sold" and new-listing counts). |
| `data/houses.json`, `houses_raw.json`, `ruleset_raw.json`, `house_breakdown.json` | Diagnostics: house list and per-house stats of the latest snapshot. |
| `data/events.json` | Market-relevant news (Wowhead RSS, filtered). |

**Nothing is ever deleted.** All history stays in the CSVs and ladder files, plus git history.

### CSV columns (`data/prices*/YYYY-MM.csv`)
`ts` (Blizzard snapshot time, UTC), `item`, `min` (lowest buyout per unit, **copper**),
`median` and `p25` (quantity-weighted, copper), `qty` (units listed), `listings`,
`est_sold`, `min_gold`, `median_gold` (readable, e.g. `13g 80s`). 10,000 copper = 1 gold.

### Ladder files (`.json.gz`)
`{"ts", "items": {name: {"ladder": [[unit_price_copper, qty, listings], ...] (sorted by price),
"depth": {units_within_5pct, units_within_10pct, gold_to_lift_10pct, avg_listing_size,
price_levels, new_listings, new_units, units_by_time_left}}}}`.
Dashboard copies use `{"l": [...], "above": [qty, listings]}`. Recording started
**25 Nov 2026, 22:26 UTC** (prices started 17:26 UTC the same day).

## Metric definitions
- **Est. sold:** units in listings that disappeared since the previous snapshot while they
  had `LONG` or `VERY_LONG` time left, plus partial stack decreases. Cancellations look the
  same, so it's rough.
- **Supply 1d / 3d / 7d:** average units listed over the last 24h vs the average over the
  24h ending 1, 3 or 7 days earlier (% change). Not a per-day average.
- **Change 1d:** same as Supply 1d in units.
- **Price 1d / 3d:** same comparison on the lowest buyout.
- **Market state** (3d if available, else 1d): Squeeze = supply ≤ −10% and price ≥ −3%;
  Sellers backing off = supply ≤ −10% and price falling; Undercut race = supply ≥ +10%
  and price ≤ −3%; Sellers chasing price = supply ≥ +10% otherwise; else Steady.
- **Signals in the items table:** Cheap vs last 7 days (lowest ≤ 0.9 × 7-day median of
  lows), Pricey (≥ 1.15×), Supply falling 3+ days (daily medians), Holding high 3 days,
  Cheaper to craft (materials < 0.85 × product).
- **Order book walls:** a price band holding ≥ 3× the median band and ≥ 15% of that hour's
  units (Bought view: ≥ 4× and ≥ 30%).

## Dashboard sections (index.html)
Item picker with ☆ favorite, tiles (lowest, median now/24h/7d, vs 7-day low, supply,
est. sold), price chart with event lines, supply chart, **Order book** heatmap
(Listed/Bought, 24h/3d/7d, walls in orange, floor line, "Listed now" depth bars),
**All items** table (category chips, ★ Favorites, sortable), **Volume** table (group,
shrinking/growing, period, min %, min listed, search, include background items),
Craft or buy, Events. Category colors: herb tiers share one purple hue; green/red are
reserved for price up/down. Favorites live in the browser's localStorage.

## Owner's trading logic (use it when analysing)
- **Volume leads price.** Supply shrinking for days while price holds or rises → demand
  exceeds supply → likely impulse up. Supply growing while price falls → undercut race
  to the bottom. Bottoms show when rising supply turns down and price stops falling.
- **Drain and refill:** a pile gets bought out, reappears higher, drains again; repeated,
  that preceded Black Lotus going ~50g → 350g in Classic.
- **Look at weeks, not days.** 1d is noise (one whale or one bad buyer), 3d a hint, 7d+ the
  signal. Always ask *why* volume is changing.
- **Bubble warning:** in a bubble, volume shrinks because everyone's buying; when it pops,
  volume spikes and price drops. Clues: price far above cost to craft/farm, growth faster
  than real use, a few huge listings (high avg listing size), parabolic chart.
- **Weekly/daily rhythm (EU materials index, 11–25 Nov):** strong daily cycle, about ±6%
  (cheapest late evening when farmers list, priciest around midday); weekends ~3% cheaper
  for materials, Monday priciest. Two weeks only: unconfirmed for US and for single items.

## Known gaps / ideas not built yet
- Gear (armor/weapons) isn't tracked; would need recording non-commodity listings broadly.
- Day × hour heatmap and pattern detection (needs ~4+ weeks of data).
- Drain-and-refill detector from ladders; depth indicators on the dashboard.
- 9 background names not found in Forever's item data (see `manifest.json` →
  `background_missing`); probably removed or renamed.

## Working on this repo
- Test changes before pushing: run `collect.py` against fake API responses, and render
  `index.html` with sample data (Chrome headless) before committing.
- Commit straight to `main` (Pages and the workflow use it). Pull/rebase first: the bot
  commits every ~30 min.
- Don't commit secrets. The Blizzard keys live only in repo secrets; the PAT only in
  cron-job.org.
- To check health: `gh run list -R TobydracoX/forever-ah-tracker -w "Collect auction prices"`
  (runs every :23/:53 with event `workflow_dispatch`), and the newest `ts` in the CSV.

## Changelog
- 2026-11-25: Tracker created (collector, workflow, dashboard). US Normal, Alliance.
- 2026-11-25: Fixed item lookup (pagination); added gold columns to CSVs; 24h/7d medians.
- 2026-11-25: Locked to house #111 after finding four rulesets' Alliance houses mixed; cleared mixed rows.
- 2026-11-25: Added herbalism groups and category colors.
- 2026-11-25: GitHub schedule never fired; cron-job.org now triggers at :23/:53.
- 2026-11-25: Tightened news filter; chart lines only for notes and item-tagged news.
- 2026-11-25: Favorites (stars, pinned section, filter).
- 2026-11-25: Full order-book ladders + depth recorded every snapshot.
- 2026-11-25: Background list (~178 endgame consumables, reagents, materials, recipes) → `data/prices-bg/`.
- 2026-11-25: Order book heatmap with walls, floor and "Listed now" bars.
- 2026-11-25: Volume table (avg listed, 1d/3d/7d supply change, price change, market state, filters).
- 2026-11-25: Added this CLAUDE.md.
