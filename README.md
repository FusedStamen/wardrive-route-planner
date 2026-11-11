# Wardrive Route Planner

A private, phone-first route planner for wardriving. You give it a start point, an optional destination, a drive-time budget and how much you care about different kinds of gain. It returns one driving route with estimated gains, a GPX track and Apple Maps legs.

It is built for the WatchDogsGo / WDGoWars game and reads live cell state from a self-hosted [warroom](#requirements) instance. It also uses two optional files built from WiGLE data: your own coverage ("cell heat") and recent network density, which lets it value cells nobody has recorded yet.

It runs as a single small Docker container (Python standard library only, one HTML page with a canvas map). It was written for a Raspberry Pi 5 behind a Cloudflare Tunnel and used from Safari on an iPhone, but nothing in it is specific to that.

## Status

Be clear about what has and has not been checked:

- Tested: the server endpoints, file validation, the page logic against a real road pack with synthetic warroom data, and the WiGLE density pull against the real WiGLE API.
- Not yet validated: the gain estimates against a real drive. The defaults in [docs/tuning.md](docs/tuning.md) were calibrated against a single before and after warroom comparison. Treat every number the page shows as an estimate, which is also what the page says.

## What you get

- One route for a time budget (15 to 600 minutes), round trip or one way, ending at home, the office, the destination or anywhere.
- Estimated gain on the route: new cells (presence), new WiFi, new BLE, XP, cell flips, plus a table of gain versus driving time so you can see where extra minutes stop paying off.
- A map of the cells coloured by owner and by value for your weights, with the route on top.
- Navigation outputs: GPX download (follows the road exactly, for OsmAnd or similar) and Apple Maps legs about 7 minutes apart.
- Address search for start and destination, "use my location", and automatic road-data builds for places you have not been before, including long corridor builds for drives between areas.
- Unvisited cells (a road but no warroom cell) are included and valued from recent WiGLE density when you provide it, from nearby charted cells when you do not.

## Requirements

- A Docker host (tested on a Raspberry Pi 5, 8 GB).
- A [warroom](#related-projects) instance and a read-only bearer token for it. The planner polls its `/api/state` every 5 minutes. The token stays on the server and is never sent to the browser.
- Internet access from the container for road routing (OSRM) and place search (Nominatim). See [Road data](#road-data).
- Optional: a WiGLE account with API access, to build the density file ([docs/density.md](docs/density.md)).

## Install

```
git clone https://github.com/FusedStamen/wardrive-route-planner.git
cd wardrive-route-planner
sed -i 's/\r$//' install.sh && bash install.sh
```

`install.sh` asks for your warroom address, the read-only token and a sign-in password, writes `.env` (permissions 600), creates the Docker network `proxy` if it does not exist, builds the image and waits for the health check. Then open `http://<host>:8090`, sign in and tap "Use my location".

To use it away from home, put it behind HTTPS. A Cloudflare Tunnel works well: add a published application route with service type HTTP and URL `routeplanner:8080`, and run `cloudflared` on the same Docker network (`proxy`). The page shows where you drive, so keep the address private.

Change the password later with `bash setpassword.sh`.

## Configuration (`.env`)

| Variable | Required | Meaning |
|---|---|---|
| `WARROOM_URL` | yes | Warroom address, no trailing slash |
| `WARROOM_TOKEN` | yes | Read-only warroom bearer token |
| `APP_PASSWORD` | yes | Sign-in password (8+ characters; no spaces and none of `# $ " ' \`) |
| `SECRET` | no | Signs the login cookie; defaults to the password. `install.sh` generates one |
| `OSRM_URL` | no | Routing server, default `https://router.project-osrm.org` |
| `OSRM_WORKERS` | no | Parallel routing requests, default 8 |
| `PORT`, `DATA_DIR` | no | Set by the Dockerfile (8080, `/data`) |

See `.env.example`. `.env` is ignored by git.

## Using it

1. Pick or search a start, optionally a destination, then set the drive-time budget and the "What matters" weights (0 to 10).
2. Tap "Plan route". Compare the gain table, the map and the "Best cells on this route" list.
3. For navigation, load the GPX in OsmAnd (or similar), or tap the Apple Maps legs in order.
4. Before a trip, open "Add an area ahead of time" and build the area you will drive in. Opening the page in a new place also offers to build road data from your location.

Weights and assumptions are explained in [docs/tuning.md](docs/tuning.md).

## Road data

Each area is a "pack": one node per 0.02 degree grid cell (about 2 km), snapped to a road point inside the cell, plus routed edges between nearby nodes and some long-range hub links so highways are used for long hops. Packs are built on the server with OSRM and saved in `data/regions/`.

- Area builds cover about 40 km around a point (10 to 50 km allowed), up to roughly 1,500 cells, including unvisited cells nearest first. They take 10 to 25 minutes.
- Corridor builds follow the driving route between two places and take 25 to 45 minutes for a long drive.
- Only one build runs at a time. Do not restart the container during a build, it kills the build.
- The default OSRM server is the public demo server, which is meant for light use. If you build many areas, run your own OSRM and set `OSRM_URL`.
- Place search uses Nominatim, rate limited to one request per 1.2 seconds with a short cache.

## WiGLE data (both optional)

| File | What it is | Used for |
|---|---|---|
| `data/wigle_cell_heat.csv` | Your own WiGLE coverage per cell, from [`wigle_cell_heat.py`](https://github.com/FusedStamen/Wigle/tree/main/wigle_cell_heat) | Knowing which cells you have already covered, which lowers expected new WiFi |
| `data/wigle_density.csv` | Recent WiFi networks WiGLE has seen per cell, from [`wigle_density.py`](https://github.com/FusedStamen/Wigle/tree/main/wigle_density) | Valuing cells nobody has recorded yet |

Both are uploaded in the page (each has its own card) or copied into `data/`. Formats are in [docs/reference.md](docs/reference.md). The density workflow, calibration and the fallback when it is missing are in [docs/density.md](docs/density.md).

## Updating

`app/` is mounted into the container read-only and live.

- Page-only changes (`app/static/index.html`): reload the browser.
- Server changes (`app/server.py`, `app/regionbuild.py`): `docker compose restart`, but never while an area build is running.
- First install, `Dockerfile` or `compose.yml` changes: `docker compose up -d --build`.

`data/` is kept between updates.

## Security and privacy

- Sign-in uses a password and a signed 30-day cookie (`HttpOnly`, `SameSite=Lax`, `Secure` when behind HTTPS). After 10 wrong passwords an address is blocked for 15 minutes.
- Every state-changing request must carry the header `X-RP: 1`, which the page adds itself.
- This is a single-user design with one shared password. Do not expose it without HTTPS.
- Your drives and coverage live in `data/` (`regions/`, `flips.json`, the two CSV files). `.gitignore` excludes `data/` and `app/seed/`. Packs can contain "Home" and "Office" points, so do not commit them.
- The warroom token is read-only and stays on the server.

## Troubleshooting

- `docker logs routeplanner` shows server errors and failed state fetches.
- "Warroom state is not available yet": the first poll has not succeeded. Check `WARROOM_URL` and the token.
- A build never finishes or fails: the OSRM server may be rate limiting. Try again later or use your own server.
- Page says no road data for this spot: build an area there (see [Road data](#road-data)).
- Windows line endings break the shell scripts: run `sed -i 's/\r$//' install.sh` first, as shown above.

## Repository layout

```
app/server.py          HTTP server, login, warroom polling, builds, file endpoints
app/regionbuild.py     road-pack builder (OSRM)
app/static/index.html  the whole page: optimizer, map, controls
Dockerfile, compose.yml
install.sh, setpassword.sh
.env.example
data/                  your files (git-ignored)
docs/                  density.md, tuning.md, reference.md
```

## Related projects

- [FusedStamen/Wigle](https://github.com/FusedStamen/Wigle): WiGLE tools. [`wigle_density`](https://github.com/FusedStamen/Wigle/tree/main/wigle_density) builds the density file for this planner, and [`wigle_cell_heat`](https://github.com/FusedStamen/Wigle/tree/main/wigle_cell_heat) builds `wigle_cell_heat.csv` from your own WiGLE exports.
- warroom: the fan-made WDGoWars companion app this planner reads state from. Add a link to the project you use here.

## License

No license has been chosen yet, so all rights are reserved by default. Add a `LICENSE` file before inviting others to reuse it.
