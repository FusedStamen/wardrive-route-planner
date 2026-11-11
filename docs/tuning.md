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
| Max new WiFi per cell | 2500 | Upper limit on the estimate for one cell |
| New WiFi guess, unvisited cell (last resort) | 1.5 | Used only when there is no density and no charted neighbor |
| WiGLE density factor (0 = auto fit) | 0 | Expected holder count per recent WiGLE network. 0 fits it from your data |
| Neighbor fallback fraction | 0.5 | Share of the nearby average holder count assumed for an unvisited cell without density |
| Neighbor radius, km | 6 | How far "nearby" reaches for that average. Three times as far is tried once, at half the fraction |
| Real-world time factor | 1.15 | Multiplies routed drive times to allow for traffic and stops |

The defaults for pass fraction, capture rates, rescan rate and BLE ratio were set from one real before and after warroom comparison. The neighbor fraction (0.5) is a reasoned guess, not a measurement. Whether XP for a capture replaces or adds to the XP for a new WiFi is unverified; the page adds them (1 per new WiFi, 10 per capture, 2 per rescan, 0.2 per BLE).

## How the estimates are built

For one pass through a cell:

- Charted cell with holder count `cnt`: `coverage = min(1, max(your APs, WiGLE WiFi nets from your heat file) / cnt)`, then `new WiFi = min(cap, pass x cnt x max(1 - coverage, 0.05))`.
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
