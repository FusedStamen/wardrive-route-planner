# Valuing unvisited cells with recent WiGLE density

## The problem

Warroom only reports cells where something has been recorded. A grid cell with a road but no warroom cell does not exist in its data. Area builds include these "unvisited" cells anyway, with status free and count 0, because gaining new ground is usually the main goal.

A single flat guess for all of them is wrong in both directions: a dense suburb and empty farmland get the same value. The flat guess (`New WiFi guess, unvisited cell`, default 1.5) is now the last resort.

## How an unvisited cell is valued

For each unvisited cell (status free, or count 0) the page picks the first source that applies:

1. **WiGLE density.** Expected holder count = density factor x recent WiFi networks WiGLE has seen in that cell. Used when a density value exists for the cell and a factor is available (fitted automatically, or set by you).
2. **Nearby charted cells.** Average holder count of charted warroom cells within the neighbor radius (default 6 km), times the neighbor fraction (default 0.5).
3. **Charted cells farther away.** The same average over three times the radius, at half the neighbor fraction.
4. **The flat guess.** Only when nothing above applies.

For 1 to 3, the expected new WiFi for one pass is `pass fraction x expected holder count`, floored at 0.1 and capped at `Max new WiFi per cell`. This is the same formula charted cells use, with no coverage by you yet.

Tapping an unvisited cell on the map says which source its estimate came from. The "Assumptions" section counts cells per source for the area on screen.

## Calibration

WiGLE counts accumulate over years and lean toward where other contributors drive. Warroom holder counts are on a different scale. So the page fits a factor from cells that have both:

- Uses charted cells held by someone (count 1 or more) that also have a density value of at least 1.
- The factor is the median of `holder count / density`, computed in log space.
- It shows the factor, the number of cells used and the range the middle half of cells fall in. If the range is wide, the absolute numbers are loose, but density still ranks cells against each other.
- It needs at least 10 such cells in the area on screen, otherwise density is not used there (unless you type a factor by hand) and the page says so. The factor is fitted again for every area, so each area gets its own.
- To override, type a number in "WiGLE density factor (0 = auto fit)".

Known bias: charted cells are the ones other players already drive a lot, so the factor probably overstates how much a quiet unvisited cell holds. If routes chase unvisited cells too eagerly, lower the factor by hand and compare with real results. See [tuning.md](tuning.md).

## Building the density file

The file is produced on a PC (or anywhere with Python) by [`wigle_density.py`](https://github.com/FusedStamen/Wigle/tree/main/wigle_density) from the WiGLE API, then uploaded in the planner page. The script's own README covers setup and options in full; the short version:

1. Fill in your WiGLE API name and token in `wigle_config.json` (the script creates the template on first run).
2. Run `python wigle_density.py --test` once. It checks that your account can use the API, that the date filter works and that totals are consistent, and writes a report with no credentials in it.
3. Run it for an area, for example `python wigle_density.py --since 20241101 --center 27.95 -82.46 --radius-km 40 --box-cells 5`.
4. Upload `wigle_density.csv` in the "Recent WiGLE density" card.

How it works: it queries WiGLE for the number of WiFi networks in boxes of N x N cells (default 3 x 3, here 5 x 5), asking only for networks last updated since a cutoff date, and spreads each box's count evenly over its cells. One query per box.

### Options that matter

| Option | Effect |
|---|---|
| `--since YYYYMMDD` | Cutoff for "recent". Default is the first of the current month, two years back. **Pin it** on every run, see below. |
| `--box-cells N` | Box size in cells. 3 is about 6.7 km, 5 about 11 km. Bigger boxes use far fewer queries but smear dense spots over sparse neighbors. 1 gives exact per-cell counts at N x N times the queries. |
| `--refine-top K` | After the coarse pass, re-query the K densest boxes cell by cell. Costs up to N x N queries per box. |
| `--max-queries N` | Stop after N queries this run (default 400). |
| `--cells FILE` | Only query boxes containing the cells listed in a CSV with `i,j` columns. |
| `--build-only` | Rebuild the CSV from the cache with no API calls. |

### Choosing the cutoff

WiGLE's `lastupdt` filter is the last time anyone re-saw a network, not when it was first found. In busy areas almost everything has been re-seen recently, so a 5-year cutoff removes very little. In one dense urban test box the counts were about 445,000 with no filter, about 443,000 for 5 years and about 228,000 for 1 year. A 1 or 2 year cutoff is much more informative. The tradeoff: rural areas are re-scanned rarely, so a short cutoff can undercount them.

### One growing file for every area

Cells are stored by global grid index (`i = floor(lat/0.02)`, `j = floor(lon/0.02)`), so one file serves every area and corridor. The script keeps every finished box in `wigle_density_cache.json` and rebuilds the CSV from the whole cache each run. Keep the cache, add areas as you need them, and upload the new CSV, which replaces the old one and still contains the earlier areas.

The cache is kept per cutoff, so **pass the same `--since` every time**. The default cutoff moves forward each month, and a run with a different cutoff starts a separate set and writes a CSV without your earlier data.

### Limits and size

- WiGLE's API limits are per account and adaptive. The script saves after every query and stops cleanly when the limit is hit, so re-run the same command later to continue. A 40 km radius with `--box-cells 5` is about 80 queries.
- The page downloads the whole density file whenever it opens. A few tens of thousands of cells is fine. Around 100,000 cells and up it will get slow on a phone, and the server would need to send only the cells near the open area.
- Query whole countries only if you have the budget for it. Pull what you will drive.

## What does not work

- **The cell heat file cannot stand in for density.** It records what you captured, not what exists. Cells you visited once have a median of 0 WiFi networks in it, and BLE is about 2.4 times WiFi, so it says nothing useful about a first visit.
- **All-time counts.** They are dominated by networks that no longer exist.

## Be honest in the UI

The page labels all of this as estimates, and so should anything you build on it. The factor is fitted on cells that other people have visited, density spreads each box evenly, and neither is a promise of what you will gain.
