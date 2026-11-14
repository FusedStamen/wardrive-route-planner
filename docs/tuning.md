# Weights, assumptions and tuning

Everything here is adjustable on the page and saved in the browser (`localStorage`). "Reset to defaults" restores the values below.

## Weights ("What matters", 0 to 10)

| Weight | Default | What it rewards |
|---|---|---|
| Cell presence | 10 | Cells you have never been in (new presence) |
| New WiFi | 4 | Expected new WiFi APs |
| New BLE | 1 | Expected new BLE |
| XP (captures, rescans) | 2 | Expected XP |
| Flips | 1 | Chance of flipping a cell held by someone else |

The score of a cell is the weighted sum of its terms. Each of the WiFi, BLE and XP terms is the cell's estimate (for XP, the capture and rescan XP only) divided by the 90th percentile of the area, capped at 1.2, so one huge cell cannot swamp the rest. Presence and flips are already 0 to 1.

Presence saturates quickly. A cell expected to give about 1.5 new WiFi is already near the full presence value, so at a high presence weight the route mostly maximizes how many new cells it touches. Density changes the ranking more through the WiFi, BLE and XP terms. If you want routes that favour rich cells over many cells, lower the presence weight.

## Assumptions

| Setting | Default | Meaning |
|---|---|---|
| Pass fraction | 0.20 | One pass through a cell finds about this share of the APs the holder has that you do not have yet |
| Capture rate, others' cells | 0.30 | Share of expected new WiFi that turns into captures in cells held by others |
| Capture rate, your cells | 0.10 | The same for cells you already hold |
| Rescan rate of your APs | 0.03 | Share of your existing APs in a cell that a pass rescans (capped at 800 per cell) |
| BLE per new WiFi | 2.6 | Expected new BLE for each new WiFi |
| Max new WiFi per cell | 3000 | Upper limit on the estimate for one cell |
| Floor, cells you hold | 0.01 | Smallest share of the holder count (before the pass fraction) still expected as new WiFi in a cell you hold. Low, because a cell you already hold nearly fully gains little on a repeat pass |
| Floor, other cells | 0.05 | The same floor for cells held by others or unheld, where a covered-looking cell can still hold unrecorded WiFi |
| New WiFi guess, unvisited cell (last resort) | 1.5 | Used only when there is no density and no charted neighbor |
| WiGLE density factor (0 = auto fit) | 0 | Expected holder count per recent WiGLE network. 0 fits it from your data |
| Neighbor fallback fraction | 0.5 | Share of the nearby average holder count assumed for an unvisited cell without density |
| Neighbor radius, km | 6 | How far "nearby" reaches for that average. Three times as far is tried once, at half the fraction |
| Real-world time factor | 1.15 | Multiplies routed drive times to allow for traffic and stops |

The defaults for pass fraction, capture rates, rescan rate and BLE ratio were set from one real before and after warroom comparison. The neighbor fraction (0.5) is a reasoned guess, not a measurement. Whether XP for a capture replaces or adds to the XP for a new WiFi is unverified; the page adds them (1 per new WiFi, 10 per capture, 2 per rescan, 0.2 per BLE).

## How the estimates are built

For one pass through a cell:

- Charted cell with holder count `cnt`: `coverage = min(1, max(your APs, WiGLE WiFi nets from your heat file) / cnt)`, then `new WiFi = min(cap, pass x cnt x max(1 - coverage, floor))`, where `floor` is the "cells you hold" floor (0.01) for your cells and the "other cells" floor (0.05) otherwise. The floors were split after two drives (2026-11-10 and 2026-11-11, 28 passes through cells you hold): the largest cell, 2118/-3561, gained 453 and then 18 on consecutive days (average about 0.2 percent of its holder count, which is pass 0.2 x floor 0.01), where the single 0.05 floor predicted about 1,100 each time. Small suburban cells on roads you had not driven before gained far more than any floor predicts (up to 570 in a cell where you held 1,084), so treat the new WiFi estimate for your own cells as a rough lower bound.
- Unvisited cell: expected holder count from [density or neighbors](density.md), then `new WiFi = min(cap, max(0.1, pass x expected count))`.
- `new BLE = BLE ratio x new WiFi`.
- `captures = capture rate x new WiFi`, with the rate depending on who holds the cell (0.05 for unheld).
- `XP = new WiFi + 0.2 x new BLE + 10 x captures + 2 x rescans`.
- `presence = 1 - exp(-2 x new WiFi)` for cells where you have no APs yet, else 0.
- `flip chance = min(1, new WiFi / gap to the holder)`, reduced for cells that changed holder recently (each change in the last 24 hours takes 20 percent off, up to 60 percent). Only for cells held by others.

The route comes from an insertion heuristic: starting from the start (and end) point it repeatedly adds the stop with the best gain per extra minute, then tidies the order with 2-opt. A cell's value counts once however many times you drive through it, so retracing a road earns nothing. The gain table re-runs the optimizer for 30, 60, 90 and 120 minutes plus your budget (only your budget for very large packs).

## Tuning from real drives

You need before and after warroom data for a drive, and the plan the page made for it.

1. Before the drive, note the planned stops and, for a few cells on the route, the estimates shown when you tap them: new WiFi, BLE, XP, chance of new presence.
2. After the drive, compare the real change in your APs per cell with the estimated new WiFi.
3. Adjust one thing at a time:
   - Gains in charted cells consistently lower or higher than estimated: change **pass fraction**.
   - Gains in unvisited cells lower or higher than estimated: change the **density factor** (or the neighbor fraction where there is no density).
   - XP off: capture and rescan rates.
   - BLE off: BLE per new WiFi.
4. Keep notes of what you changed. The values are stored per browser, so set them on each device you use.

Tuning on a handful of cells is noisy. Prefer the dense cells where the numbers are large.


## Road style, highways and map tiles

**Roads setting.** Each road edge gets a highway-speed share from its average speed (distance over drive time): 0 at 45 km/h or slower, 1 at 85 km/h or faster. This works from speed alone, so a business-lined state route that crawls between lights counts as a normal road while a barren interstate counts as highway.

- *Local roads*: highway-speed edges cost 4x the time when choosing paths (penalty 3.0) and lose 85 percent of their coverage credit. Reported minutes are always the real drive time.
- *Balanced*: penalty 0.5, credit loss 60 percent.
- *Transit*: no time penalty, credit loss 80 percent. Highways stay cheap for getting between stops but earn little, so the planner picks stops worth leaving the highway for. Use it with a destination and the "direct +30 / +60 / +120" chips.

The values are in `HMODES` and the speed thresholds in `HW0` and `HW1` near the top of the page script. Changing the Roads setting re-runs the all-pairs path search, which takes a moment on large areas.

**Readouts.** The summary tiles now show miles driven, highway-speed miles (the speed-based estimate above) and retraced road (the share of edge distance driven more than once). Retraced time is already counted in the drive minutes, so the optimizer pays for it; the readout is there so you can see it.

**Stops and what each adds.** For each stop, the page re-scores the route without it and shows the minutes saved and score lost. Stops marked weak earn less than half the median score per minute. Stops that lie on the way and cost no time are hidden.

**Map tiles.** The Tiles button draws Stadia Maps basemap tiles (Alidade Smooth, with a dark version in dark mode) under the cells so streets and names show. Paste your Stadia Maps API key into the box under the map and press Save key. The key is stored only in that browser's local storage, never on the Pi or in this repository, so repeat it on each device. If your Stadia property uses domain authentication, add the planner's address there. Tiles need internet in the browser. Without a working key the plain road lines are shown. Attribution is drawn on the map as Stadia Maps requires.

**Saved settings.** Assumption values you edited are stored in the browser and override new defaults, so each browser or device keeps its own. An old saved 0.001 for "Floor, cells you hold" is replaced by 0.01 when the page loads. To return everything to the current defaults, press Reset to defaults.
