# Reference: endpoints and data files

## Grid

Cells are 0.02 degrees, about 2 km. `i = floor(lat / 0.02)`, `j = floor(lon / 0.02)`. A cell's centre is `((i + 0.5) x 0.02, (j + 0.5) x 0.02)`. Every file below uses these indexes.

## HTTP endpoints

All endpoints except `/healthz` and `/login` need the sign-in cookie (`rp_session`) and answer 401 without it. Every POST except `/login` also needs the header `X-RP: 1` (403 without it).

| Method and path | Purpose |
|---|---|
| `GET /healthz` | Plain `ok`, no sign-in. Used by the Docker health check |
| `GET /login`, `POST /login` | Sign-in form and submit |
| `GET /` | The page |
| `GET /api/regions` | Saved areas, build status, warroom data time |
| `GET /api/region/<id>` | One pack with live cell state added (see below). `id` matches `[a-z0-9-]{1,60}` |
| `POST /api/build` | Start a build. Body, one of: `{"lat","lon","name","radius"}` (area, radius clamped to 10 to 50 km, default 40), `{"query": "place name"}` (geocoded first), or `{"corridor": {"a": [lat, lon, name], "b": [lat, lon, name]}}`. Returns 409 if a build is already running |
| `GET /api/build/status` | `running`, `id`, `name`, `step`, `done`, `total`, `error` |
| `GET /api/geocode?q=...` | Up to 5 place matches (Nominatim, at least 3 characters) |
| `GET /api/locate?lat=..&lon=..` | Saved areas that cover a point, best match first |
| `GET /api/heat`, `POST /api/heat` | Cell heat file. POST body is the CSV, must have columns `i`, `j`, `wifi_nets` |
| `GET /api/density`, `POST /api/density` | Density file. POST body is the CSV, must have columns `i`, `j`, `wifi_recent` and at least one usable row |

`GET /api/heat` returns `{"at": ISO time, "rows": [[i, j, wifi_nets, ble_nets, nets], ...]}`. `GET /api/density` returns `{"at": ISO time, "rows": [[i, j, wifi_recent], ...]}`. Both return empty rows when no file is stored.

Uploads are limited to 60 MB.

## Live state added to a pack

`GET /api/region/<id>` returns the stored pack plus:

- `attrs`: one entry per node, `[status, count, my_aps, gap, flips_24h, gang_index]`. Status is 0 yours, 1 held by others, 2 free or not in warroom (unvisited), 3 not a cell (extra nodes such as Home).
- `gangs`: gang names indexed by `gang_index`.
- `asof`: time of the last warroom poll.

Warroom is polled every 5 minutes. A change of status or gang between polls is recorded as a flip and kept for 24 hours in `data/flips.json`.

## Files in `data/`

Everything here is yours and git-ignored.

### `wigle_cell_heat.csv`

Your own WiGLE coverage per cell. Header:

```
i,j,lat,lon,obs,nets,wifi_nets,ble_nets,files,first,last
```

Only `i`, `j` and `wifi_nets` are required for upload. `ble_nets` and `nets` are used if present. The page treats `wifi_nets` as WiFi networks you have already captured in the cell. Replace it every 60 days (30 to 90 works). Generate it from your WiGLE exports with your own tooling.

### `wigle_density.csv`

Recent WiGLE WiFi networks per cell. Header:

```
i,j,lat,lon,wifi_recent
```

Only `i`, `j` and `wifi_recent` are required for upload. `wifi_recent` is a count (can be fractional because box counts are spread over cells). Produced by `wigle_density.py`, see [density.md](density.md).

### `regions/<id>.json`

One road pack per area. Top-level fields:

| Field | Meaning |
|---|---|
| `id`, `name`, `lat`, `lon`, `radius`, `built` | Identity, centre, radius in km, build time (UTC) |
| `kind`, `width`, `alat`, `alon`, `aname`, `blat`, `blon`, `bname`, `route` | Corridor packs only: width in km, the two ends, and the driving route as an encoded polyline |
| `nodes` | `[i, j, lat, lon]` per cell, with the road point the cell was snapped to. Extra named nodes are `[null, null, lat, lon, "Name"]`, for example `"Home"` and `"Office"` |
| `edges` | `[node_a, node_b, seconds, metres, polyline]` per routed link. The polyline is Google-encoded at precision 5 |

The page selects the Home and Office start and trip options from nodes named exactly `Home` and `Office`. Area builds started from the page do not create them. To use them, build a pack with `regionbuild.build(..., extras=[("Home", lat, lon), ("Office", lat, lon)], ...)` or add the nodes to a pack file. Packs can therefore contain your home and work coordinates, so never commit them.

### `flips.json`

Recent holder changes per cell, `{"i,j": [unix times]}`, pruned to the last 24 hours.

## Pack size limits

- Area builds: roughly 1,500 cells.
- Corridor builds: up to 2,200 cells. The corridor width starts at 12 km and narrows in 2 km steps (to about 4 to 5 km at the least) to stay under the cap.
- Merging two saved areas into one pack is not implemented.
