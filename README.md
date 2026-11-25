# WoW Forever auction house tracker

Collects auction house prices for a watchlist every hour and shows them on a dashboard page.

- `collect.py` pulls the hourly snapshot from Blizzard's API (namespace `dynamic-forever-us`, ruleset 2 = Normal) and appends one row per item to `data/prices/YYYY-MM.csv`.
- `.github/workflows/collect.yml` runs it every hour on GitHub and saves the data back to the repo.
- `index.html` is the dashboard (served by GitHub Pages).
- `config.json` is your settings: region, ruleset, faction, watchlist, recipes, news feeds.
- `notes.json` is your own event notes; they show as markers on the charts.

## Setup (about 15 minutes)

### 1. Blizzard API keys
1. Go to https://develop.battle.net/access/clients and sign in with your Battle.net account.
2. Click **Create Client**. Name it anything (for example `ah-tracker`). Leave the redirect URL empty, and check that it's not used for a service.
3. Copy the **Client ID** and **Client Secret**. Keep the secret private.

### 2. GitHub repo
1. On GitHub, click **New repository**. Name it `forever-ah-tracker`, set it to **Public** (free GitHub Pages needs public), and create it.
2. Click **uploading an existing file**, then drag in everything from this folder **except** the `.github` folder (it's hidden on some systems, so add it in the next step). Commit.
3. Click **Add file → Create new file**, type `.github/workflows/collect.yml` as the name, paste the contents of that file, and commit.

### 3. Secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**, twice:
- `BLIZZARD_CLIENT_ID`: your client ID
- `BLIZZARD_CLIENT_SECRET`: your client secret

### 4. Turn on the dashboard
Repo → **Settings → Pages** → Source: **Deploy from a branch**, Branch: `main`, folder `/ (root)` → Save.
Your page will be at `https://<your-username>.github.io/forever-ah-tracker/`.

### 5. First run
Repo → **Actions** → **Collect auction prices** → **Run workflow**. It takes about a minute.
- Green check: done. Open the dashboard. From now on it runs every hour by itself.
- Red X: open the run, copy the log and send it to Claude.

## Everyday use
- **Add or remove items:** edit `config.json` → `watchlist` on GitHub. Names must match the in-game names exactly.
- **Add a note:** edit `notes.json`, for example `{"date": "2026-12-15", "title": "Guild started Onyxia", "items": []}`. Put item names in `items` to show the note only on those items.
- **Faction:** the first run saves the list of auction houses to `data/houses.json`. If the tracker picked the wrong ones, put the right ids in `house_ids` in `config.json`.

## Notes
- Prices are per unit, in copper (the page shows g/s/c).
- "Est. sold" counts listings that disappeared while they still had a long time left, plus partly bought stacks. Cancelled auctions look the same, so treat it as a rough signal.
- Blizzard refreshes the data about once an hour. If nothing changed, the run records nothing.
- Blizzard doesn't officially document the Forever API. If it changes, the run will fail and the log will say why.

## Price ladders (backend only)
Every snapshot also saves the full price ladder for each watched item to
`data/ladders/YYYY-MM/<snapshot>.json.gz`: each price level with its quantity and
listing count, plus depth numbers (units within 5% and 10% of the lowest price, gold
needed to lift the price 10%, average listing size, new listings since the last
snapshot, units by time left). Nothing on the dashboard uses these yet; they're
there so depth and supply indicators can be built and back-tested later.
