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
- Hunter, Alliance, US Normal. Level ~37 (Nov 26). Herbalism 225 + Skinning; plans to
  drop Skinning for Alchemy and run Transmute: Arcanite at 275 (recipe 5g, Alchemist
  Pestlezugg, Gadgetzan). Limited play time. Interested in raid consumables, scarce materials,
  and supply/volume-based trading.
- Prefers small, checkable steps; wants data kept forever; dashboard kept uncluttered
  (extra items tracked in the background, not shown).
- Timezone: America/Halifax (UTC−4). Talk to them in local time.

## How it runs
1. **Trigger:** cron-job.org POSTs GitHub's workflow_dispatch API at **:23 and :53** every
   hour (`https://api.github.com/repos/TobydracoX/forever-ah-tracker/actions/workflows/collect.yml/dispatches`,
   body `{"ref":"main"}`, header `Authorization: Bearer <fine-grained PAT, Actions: read+write,
   this repo only, no expiry>`). GitHub's own `schedule` in the workflow never fired for this
   repo, so cron-job.org is the real scheduler. If runs stop with 401/403, the PAT was
   revoked or lost access and needs regenerating and pasting into cron-job.org.
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
  "Blackwater Auction House". The API does **not** say which ruleset a house belongs to
  (every ruleset returns identical data). They come in Alliance/Horde/Blackwater triplets:
  110/113/116, **111/114/117**, 112/115/118 (empty), 119/120/121, 123/124/125.
  **US Normal Alliance = 111**: outof.games labels it so, and wowfah.com's US Normal
  Alliance prices matched house 111 exactly (26 Nov). `diag_houses.py` (Actions →
  "Compare auction houses") writes lowest prices per house to `data/house_compare.json`.
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
| `data/market/YYYY-MM/<ts>.csv.gz` | **Whole-market scan:** one row per commodity on house 111 each snapshot, plus every non-stackable item of class **Recipe** (recipes, patterns, formulas, plans, schematics; from 26 Nov 03:xx local); gear is looked up and cached but not saved: `item_id,min,median,p25,qty,listings` (copper). No order books. Started 26 Nov 2026. |
| `data/market_items.json` | Item id → `{name, class}` for every item seen on the house, gear included (the dashboard search hides Armor/Weapon) (looked up via `/data/wow/item/{id}`, cached). |
| `data/manifest.json` | What the page loads: months, categories, background_categories, ladder file list (last 31 days), market file list (last 8 days), missing names, `last_snapshot`, `updated` (last run time). |
| `data/items.json` | Name → item IDs cache. |
| `data/state.json` | Last snapshot's listing IDs/qty/time_left (for "est. sold" and new-listing counts). |
| `data/houses.json`, `houses_raw.json`, `ruleset_raw.json`, `house_breakdown.json` | Diagnostics: house list and per-house stats of the latest snapshot. |
| `data/events.json` | Market-relevant news (Wowhead RSS, filtered). |
| `data/recipes.json` | Every profession recipe in Forever (2,495): profession, skill category, crafted item, reagents with quantities. From `recipes.py`. |
| `data/reagent_uses.json` | Item name → `{recipes, by_profession, used_in[]}`: how many recipes use it and which. 588 reagents. |
| `recipes.py` + `.github/workflows/recipes.yml` | Recipe crawler, run by hand (Actions → "Update recipe data") after patches. |

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
Health warning at the top (shown if the last run is > 2h old, or the newest Blizzard
snapshot is > 3h old). **Big movers** (first card): every dashboard and background item
(optionally the whole market) whose latest-snapshot price or units listed differs from
its median over the last 24h/3d/7d by at least N%; filters for scope, metric (price /
price or listed / units listed), direction, period, min %, min usual price (default 10s),
min usual listed (default 10), search; sortable; "What happened" label (Bought out =
price up + listings down, Dumped = price down + listings up, Price jump/drop, Flooding,
Drying up, Sold out); top 20 with Show all; row click opens the chart. Item picker (★ Favorites group first, then searched items, then
categories) with ☆ favorite and a **search box over every tracked item**: dashboard,
background (loads `prices-bg`) and whole-market items (loads the last 8 days of
`data/market` files; prices and supply only, no order book, no est. sold); tiles (lowest, median now/24h/7d, vs 7-day low, supply,
est. sold), price chart with event lines, supply chart, **Transmute monitor** (for each
`Bar` recipe in `config.recipes`: bar price, materials cost, profit after the AH cut,
margin, 24h average profit, bars listed, supply 1d; a floor-wall line from the newest
`ladders-dash` file (units and sellers at the floor, % within 5%); chart of bar price
after cut vs materials cost; history merges dashboard, `prices-bg` and `data/market`
rows), **Order book** heatmap
(Listed/Bought, 24h/3d/7d, walls in orange, floor line, "Listed now" depth bars),
**All items** table (category chips, ★ Favorites, sortable), **Volume** table (group,
shrinking/growing, period, min %, min listed, search, include background items),
**Inventory** (owner logs buys, **harvested** items (auto-priced at the current lowest buyout, i.e. opportunity cost) and sells: running average cost, holdings, value at current lowest buyout after an adjustable AH cut (default 5%), open and realized profit; Edit on a holding adds a "Set holding / avg cost" (`adjust`) entry that overrides quantity and average cost from that time on; Edit on any transaction changes it in place; stored in localStorage key `ah-inventory`, with Export/Import JSON), **Playbook** (the owner's rules below, written out), Craft or buy, Events. Every section has a Minimize/Expand button (plus Collapse all / Expand all at the top); which sections are folded is remembered in localStorage. Category colors: herb tiers share one purple hue; green/red are
reserved for price up/down. Favorites live in the browser's localStorage.

## Answering questions about items (owner's standard)
When the owner asks about an item, cover these, using this repo's data first:
1. **What it is and where it comes from** (herb/ore/drop/crafted; zone or level range; gatherer skill).
2. **How many recipes use it** and in which professions, from `data/reagent_uses.json`
   (and *indirect* uses: e.g. Frilled Lichen → Cerulean Dye, which is in 131 recipes).
3. **Tier: leveling, mid, late or endgame?** Judge by the level of what it makes.
4. **Repeat customers?** Is what it makes used up (potions, elixirs, food, oils, enchants =
   recurring demand) or bought once (gear, bags, profession leveling = demand that fades)?
5. **Current market** from the CSVs: lowest/median price, units listed, listings, trend and
   Market state; compare to similar items.
6. **Verdict for the owner**: sell now, hold, bank for Alchemy, or farm more, with the reason.

**Demand themes: always walk the whole supply chain.** When the owner asks about a theme
(fire resistance, raid prep, a new dungeon, a holiday), list every input behind it, not
just the ones they can farm or craft, with the current price and usual price for each,
and flag any that are cheap right now. (Lesson, Nov 26: asked about fire resistance, we
covered Fire Protection potions and Dreamfoil but missed Heart of Fire, which sat at
70–80s overnight and jumped to 3g the next morning.) Also check the Big movers section
before answering any "what's happening in the market" question.

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
- Gear (armor/weapons) isn't tracked (owner said ignore for now). The market scan covers commodities only.
- Blackwater (neutral) and Horde houses aren't recorded; discussed as a cross-faction arbitrage idea.
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
- 2026-11-26: Minimize/Expand buttons on every dashboard section, Collapse all / Expand all, remembered per browser.
- 2026-11-26: Market scan now includes every recipe-type item; background name fixed: Major Troll's Blood Potion → Major Troll's Blood Elixir (Forever renamed several potions to elixirs).
- 2026-11-26: Inventory editing: holdings (set quantity/avg cost) and transactions.
- 2026-11-26: House comparison check; found houses don't report their ruleset (triplets noted above).
- 2026-11-26: Inventory "Harvested" type, priced at the current lowest buyout; tile for total harvested value.
- 2026-11-26: Recipe crawler: `data/recipes.json` (2,495 recipes) and `data/reagent_uses.json`; added the item-question standard.
- 2026-11-26: Inventory section (average-cost tracking, P/L, export/import) and Playbook section.
- 2026-11-26: Whole-market scan of every commodity (`data/market/`, names in `market_items.json`); search box for any tracked item; favorites at the top of the item picker; health warning banner. PAT has no expiry.
- 2026-11-26: Transmute group (Arcanite Bar, Arcane Crystal, Legionite Bar, Fel Crystal, Heavy Thorium Bar) on the dashboard; Arcanite and Legionite transmute recipes; Transmute monitor section; market-item history now merges into existing history instead of being skipped.
- 2026-11-26: Big movers section (price/listing anomaly scanner with filters) at the top of the dashboard; supply-chain rule for demand-theme questions; fixed a race where two sections loading background data at once could see it empty.
