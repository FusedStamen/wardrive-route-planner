"""Builds a road-network pack for one area using an OSRM server.

A pack holds one node per 0.02 degree grid cell (snapped to a road point inside the cell)
and routed edges (drive time, distance, simplified road geometry) between nearby nodes
plus a set of long-range hub links so highways are used for long hops.
"""
import json, math, time, os, urllib.request, concurrent.futures as cf

UA = {"User-Agent": "curl/8.5.0"}
OSRM = os.environ.get("OSRM_URL", "https://router.project-osrm.org").rstrip("/")
WORKERS = int(os.environ.get("OSRM_WORKERS", "8"))


def hav(la1, lo1, la2, lo2):
    p = math.pi / 180
    x = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def get(url, tries=5):
    for t in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.load(r)
        except Exception:
            time.sleep(1 + 2 * t)
    return None


def enc(pts):
    out = []; pl = pa = 0
    for la, lo in pts:
        a = round(la * 1e5); o = round(lo * 1e5)
        for v in (a - pl, o - pa):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63)); v >>= 5
            out.append(chr(v + 63))
        pl, pa = a, o
    return "".join(out)


def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    (y1, x1), (y2, x2) = pts[0], pts[-1]
    cy = math.cos(math.radians(y1))
    ax, ay = (x2 - x1) * cy * 111320, (y2 - y1) * 111320
    L = ax * ax + ay * ay
    dmax = 0; idx = 0
    for i in range(1, len(pts) - 1):
        py, px = pts[i]
        bx, by = (px - x1) * cy * 111320, (py - y1) * 111320
        if L == 0:
            d = math.hypot(bx, by)
        else:
            t = max(0, min(1, (bx * ax + by * ay) / L)); d = math.hypot(bx - t * ax, by - t * ay)
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return rdp(pts[:idx + 1], eps)[:-1] + rdp(pts[idx:], eps)
    return [pts[0], pts[-1]]


def build(pack_id, name, lat, lon, radius_km, state_cells, extras, out_path, progress,
          meta=None, tail=None, n_hubs=34, workers=None, uncharted=None):
    """state_cells: iterable of (i, j). extras: list of (name, lat, lon). Writes out_path.
    radius_km=None means state_cells is already the final selection (corridor builds).
    meta: extra small fields stored before the nodes. tail: extra fields stored after the edges."""
    WORKERS_ = workers or WORKERS
    cells = []
    for (i, j) in state_cells:
        clat, clon = (i + .5) * .02, (j + .5) * .02
        if radius_km is None or hav(lat, lon, clat, clon) <= radius_km:
            cells.append((i, j))
    strict = set()
    for (i, j) in (uncharted or ()):
        clat, clon = (i + .5) * .02, (j + .5) * .02
        if (i, j) not in cells and (radius_km is None or hav(lat, lon, clat, clon) <= radius_km):
            cells.append((i, j)); strict.add((i, j))
    cells.sort()
    if len(cells) < 3:
        raise RuntimeError("Warroom has too few cells within %d km of that point." % radius_km)
    nodes = [dict(i=i, j=j, strict=((i, j) in strict)) for i, j in cells] + [dict(i=None, j=None, nm=e[0], la0=e[1], lo0=e[2]) for e in extras]
    n = len(nodes)

    # 1. snap each cell centre to a road point inside the cell
    progress("Snapping cells to roads", 0, n)
    done = [0]

    def snap(nd):
        if nd["i"] is None:
            la, lo, b = nd["la0"], nd["lo0"], None
        else:
            la, lo = (nd["i"] + .5) * .02, (nd["j"] + .5) * .02
            b = (nd["i"] * .02, (nd["i"] + 1) * .02, nd["j"] * .02, (nd["j"] + 1) * .02)
        r = get(f"{OSRM}/nearest/v1/driving/{lo},{la}?number=10")
        done[0] += 1
        if done[0] % 25 == 0:
            progress("Snapping cells to roads", done[0], n)
        if not r or r.get("code") != "Ok":
            return None
        best = None
        for w in r["waypoints"]:
            x, y = w["location"]
            if b and b[0] <= y < b[1] and b[2] <= x < b[3]:
                best = (y, x); break
        if best is None and nd.get("strict"):
            return None
        if best is None:
            w = r["waypoints"][0]; best = (w["location"][1], w["location"][0])
        return best

    with cf.ThreadPoolExecutor(WORKERS_) as ex:
        sn = list(ex.map(snap, nodes))
    keep = [k for k in range(n) if sn[k] is not None]
    nodes = [nodes[k] for k in keep]; sn = [sn[k] for k in keep]
    n = len(nodes)
    for nd, s in zip(nodes, sn):
        nd["la"], nd["lo"] = round(s[0], 5), round(s[1], 5)

    # 2. choose edges: 5 nearest neighbours, hub-to-hub links, extras to 12 nearest
    D = lambda a, b: hav(nodes[a]["la"], nodes[a]["lo"], nodes[b]["la"], nodes[b]["lo"])
    pairs = set()
    for a in range(n):
        for _, b in sorted((D(a, b), b) for b in range(n) if b != a)[:5]:
            pairs.add((min(a, b), max(a, b)))
    first = next((x for x in range(n) if nodes[x].get("nm")), 0)
    hubs = [first]; dm = [D(first, x) for x in range(n)]
    for _ in range(min(n_hubs, n - 1)):
        h = max(range(n), key=lambda x: dm[x]); hubs.append(h)
        dm = [min(dm[x], D(h, x)) for x in range(n)]
    for x in hubs:
        for y in hubs:
            if x < y:
                pairs.add((x, y))
    for e in range(n):
        if nodes[e].get("nm"):
            for _, b in sorted((D(e, b), b) for b in range(n) if b != e)[:12]:
                pairs.add((min(e, b), max(e, b)))
    pairs = sorted(pairs)

    def route(p):
        a, b = p
        u = f"{OSRM}/route/v1/driving/{nodes[a]['lo']},{nodes[a]['la']};{nodes[b]['lo']},{nodes[b]['la']}?overview=full&geometries=geojson&continue_straight=false"
        r = get(u, tries=4)
        if not r or r.get("code") != "Ok":
            return None
        rt = r["routes"][0]
        pts = [(y, x) for x, y in rt["geometry"]["coordinates"]]
        return [a, b, round(rt["duration"]), round(rt["distance"]), enc(rdp(pts, 6))]

    progress("Routing roads", 0, len(pairs))
    edges = []
    with cf.ThreadPoolExecutor(WORKERS_) as ex:
        for k, r in enumerate(ex.map(route, pairs)):
            if r:
                edges.append(r)
            if k % 50 == 0:
                progress("Routing roads", k, len(pairs))

    # 3. keep the largest connected piece, trying to join stray pieces
    def components():
        par = list(range(n))
        def f(x):
            while par[x] != x:
                par[x] = par[par[x]]; x = par[x]
            return x
        for a, b, *_ in edges:
            par[f(a)] = f(b)
        comp = {}
        for x in range(n):
            comp.setdefault(f(x), []).append(x)
        return sorted(comp.values(), key=len, reverse=True)

    comps = components()
    for stray in comps[1:]:
        main = set(comps[0])
        cand = sorted(((D(a, b), a, b) for a in stray for b in main))[:6]
        for _, a, b in cand:
            r = route((min(a, b), max(a, b)))
            if r:
                edges.append(r); break
    comps = components()
    main = set(comps[0])
    remap = {old: new for new, old in enumerate(sorted(main))}
    nodes = [nodes[o] for o in sorted(main)]
    edges = [[remap[a], remap[b], t, d, p] for a, b, t, d, p in edges if a in main and b in main]
    if len(nodes) < 3 or not edges:
        raise RuntimeError("Road routing failed for this area. Try again later.")

    pack = dict(id=pack_id, name=name, lat=lat, lon=lon, radius=radius_km if radius_km is not None else (meta or {}).get("width", 0),
                built=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    pack.update(meta or {})
    pack["nodes"] = [[nd["i"], nd["j"], nd["la"], nd["lo"]] + ([nd["nm"]] if nd.get("nm") else []) for nd in nodes]
    pack["edges"] = edges
    pack.update(tail or {})
    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(pack, f, separators=(",", ":"))
    os.replace(tmp, out_path)
    progress("Done", 1, 1)
    return pack
