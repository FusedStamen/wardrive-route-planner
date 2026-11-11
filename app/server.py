#!/usr/bin/env python3
"""Wardrive route planner server. Python standard library only.

- Keeps the warroom read-only token on the server; the browser never sees it.
- Serves the page, region road packs, live cell state, flip history and the heat file.
- Builds road packs for new areas on demand using an OSRM server.
"""
import os, sys, math, json, time, hmac, hashlib, threading, gzip, re, shutil, urllib.request, urllib.parse, csv, io, glob, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regionbuild

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))
WARROOM = os.environ.get("WARROOM_URL", "").rstrip("/")
TOKEN = os.environ.get("WARROOM_TOKEN", "")
PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET = (os.environ.get("SECRET") or PASSWORD).encode()
POLL_SECONDS = 300
ST = {"mine": 0, "enemy": 1, "free": 2}
UA = {"User-Agent": "curl/8.5.0"}

if not PASSWORD or not TOKEN or not WARROOM:
    sys.exit("APP_PASSWORD, WARROOM_TOKEN and WARROOM_URL must be set (see .env.example).")
os.makedirs(os.path.join(DATA, "regions"), exist_ok=True)
for f in glob.glob(os.path.join(HERE, "seed", "*.json")):
    dst = os.path.join(DATA, "regions", os.path.basename(f))
    if not os.path.exists(dst):
        shutil.copy(f, dst)

# ---------- warroom state and flip history ----------
STATE = {"cells": {}, "asof": None, "fetched": 0, "error": None}
FLIPS = {}
FLIPS_FILE = os.path.join(DATA, "flips.json")
SLOCK = threading.Lock()
try:
    FLIPS = {tuple(map(int, k.split(","))): v for k, v in json.load(open(FLIPS_FILE)).items()}
except Exception:
    FLIPS = {}


def fetch_state():
    req = urllib.request.Request(WARROOM + "/api/state", headers=dict(UA, Authorization="Bearer " + TOKEN))
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.load(r)
    cells = {}
    for c in data["cells"]:
        cells[(c["i"], c["j"])] = (ST.get(c["status"], 2), c["count"] or 0, c["my_aps"] or 0, c["gap"] or 0, c["towers"] or 0, c["gang"] or "")
    now = time.time()
    with SLOCK:
        prev = STATE["cells"]
        if prev:
            for k, v in cells.items():
                p = prev.get(k)
                if p is not None and (p[0], p[5]) != (v[0], v[5]):
                    FLIPS.setdefault(k, []).append(now)
        for k in list(FLIPS):
            FLIPS[k] = [t for t in FLIPS[k] if now - t < 86400]
            if not FLIPS[k]:
                del FLIPS[k]
        STATE.update(cells=cells, asof=data["meta"].get("last_poll"), fetched=now, error=None)
    try:
        json.dump({"%d,%d" % k: v for k, v in FLIPS.items()}, open(FLIPS_FILE, "w"))
    except Exception:
        pass


def poll_loop():
    while True:
        try:
            fetch_state()
        except Exception as e:
            STATE["error"] = str(e)
            print("state fetch failed:", e, flush=True)
        time.sleep(POLL_SECONDS)


def get_state():
    if time.time() - STATE["fetched"] > 2 * POLL_SECONDS:
        try:
            fetch_state()
        except Exception as e:
            STATE["error"] = str(e)
    if not STATE["cells"]:
        raise RuntimeError("Warroom state is not available yet: " + str(STATE["error"]))
    return STATE


# ---------- regions ----------
def region_path(rid):
    if not re.fullmatch(r"[a-z0-9-]{1,60}", rid or ""):
        return None
    p = os.path.join(DATA, "regions", rid + ".json")
    return p if os.path.exists(p) else None


def list_regions():
    out = []
    for f in sorted(glob.glob(os.path.join(DATA, "regions", "*.json"))):
        try:
            with open(f) as fh:
                head = fh.read(1500)
            m = {k: re.search(r'"%s":("[^"]*"|[-0-9.]+)' % k, head) for k in ("id", "name", "lat", "lon", "radius", "built", "kind", "width")}
            out.append({k: json.loads(v.group(1)) for k, v in m.items() if v})
        except Exception:
            pass
    return out


def region_payload(rid):
    p = region_path(rid)
    if not p:
        return None
    pack = json.load(open(p))
    st = get_state()
    now = time.time()
    gangs, gi, attrs = [], {}, []
    for nd in pack["nodes"]:
        c = st["cells"].get((nd[0], nd[1])) if nd[0] is not None else None
        if c is None:
            attrs.append([3 if nd[0] is None else 2, 0, 0, 0, 0, 0]); continue
        g = c[5]
        if g not in gi:
            gi[g] = len(gangs); gangs.append(g)
        fl = len([t for t in FLIPS.get((nd[0], nd[1]), []) if now - t < 86400])
        attrs.append([c[0], c[1], c[2], c[3], fl, gi[g]])
    pack["attrs"] = attrs
    pack["gangs"] = gangs or [""]
    pack["asof"] = (st["asof"] or "") + " UTC"
    return pack


# ---------- place search (Nominatim, cached and rate limited) ----------
NOM_UA = {"User-Agent": "wardrive-route-planner/1.0 (personal use)"}
_geo_cache = {}
_geo_lock = threading.Lock()
_geo_last = [0.0]


def nominatim(url):
    with _geo_lock:
        wait = 1.2 - (time.time() - _geo_last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=NOM_UA), timeout=20) as r:
                return json.load(r)
        finally:
            _geo_last[0] = time.time()


def short_name(display):
    parts = [x.strip() for x in display.split(",")]
    if not parts:
        return "Place"
    n = ", ".join(parts[:2]) if (parts[0].isdigit() or len(parts[0]) < 4) and len(parts) > 1 else parts[0]
    return n[:60]


def search_places(q):
    key = q.strip().lower()
    if key in _geo_cache:
        return _geo_cache[key]
    res = nominatim("https://nominatim.openstreetmap.org/search?format=json&limit=5&q=" + urllib.parse.quote(q))
    out = [{"name": short_name(r["display_name"]), "display": r["display_name"][:140], "lat": float(r["lat"]), "lon": float(r["lon"])} for r in res]
    if len(_geo_cache) > 300:
        _geo_cache.clear()
    _geo_cache[key] = out
    return out


def reverse_name(lat, lon):
    try:
        r = nominatim("https://nominatim.openstreetmap.org/reverse?format=json&zoom=10&lat=%f&lon=%f" % (lat, lon))
        a = r.get("address") or {}
        for k in ("city", "town", "village", "municipality", "county", "state"):
            if a.get(k):
                return a[k][:60]
        return short_name(r.get("display_name", "New area"))
    except Exception:
        return "New area"


# ---------- coverage: which saved areas cover a point ----------
_route_cache = {}


def route_points(rid):
    p = region_path(rid)
    if not p:
        return []
    mt = os.path.getmtime(p)
    c = _route_cache.get(rid)
    if c and c[0] == mt:
        return c[1]
    try:
        enc = json.load(open(p)).get("route") or ""
    except Exception:
        enc = ""
    pts = decode_poly(enc)
    _route_cache[rid] = (mt, pts)
    return pts


def decode_poly(s):
    out = []; i = la = lo = 0
    while i < len(s):
        for k in (0, 1):
            r = sh = 0
            while True:
                b = ord(s[i]) - 63; i += 1
                r |= (b & 0x1f) << sh; sh += 5
                if b < 0x20:
                    break
            d = ~(r >> 1) if r & 1 else r >> 1
            if k == 0:
                la += d
            else:
                lo += d
        out.append((la / 1e5, lo / 1e5))
    return out


def dist_to_route_km(pts, lat, lon):
    best = 1e9
    cl = math.cos(math.radians(lat))
    for a, b in zip(pts, pts[1:]):
        ax, ay = (a[1] - lon) * cl * 111.32, (a[0] - lat) * 111.32
        bx, by = (b[1] - lon) * cl * 111.32, (b[0] - lat) * 111.32
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def covering(lat, lon):
    out = []
    for r in list_regions():
        try:
            if r.get("kind") == "corridor":
                d = dist_to_route_km(route_points(r["id"]), lat, lon)
                lim = float(r.get("width") or 10) * 0.9
                if d <= lim:
                    out.append({"id": r["id"], "name": r.get("name"), "kind": "corridor", "score": d / max(lim, 1)})
            else:
                d = regionbuild.hav(r["lat"], r["lon"], lat, lon)
                lim = float(r.get("radius") or 30) * 0.9
                if d <= lim:
                    out.append({"id": r["id"], "name": r.get("name"), "kind": "area", "score": d / max(lim, 1)})
        except Exception:
            continue
    out.sort(key=lambda x: x["score"])
    return out


# ---------- corridor builds ----------
def start_corridor(a, b):
    """a, b: (lat, lon, name). Builds a pack along the driving route between them."""
    with BLOCK:
        if BUILD["running"]:
            return False
        nm = "%s to %s" % (a[2], b[2])
        rid = "%s-c%d-%d" % (slug(nm), round(abs(b[0]) * 10), round(abs(b[1]) * 10))
        BUILD.update(running=True, id=rid, name=nm[:60], step="Finding the driving route", done=0, total=0, error=None)

    def progress(step, done, total):
        BUILD.update(step=step, done=done, total=total)

    def work():
        try:
            u = "%s/route/v1/driving/%f,%f;%f,%f?overview=full&geometries=geojson" % (regionbuild.OSRM, a[1], a[0], b[1], b[0])
            r = regionbuild.get(u)
            if not r or r.get("code") != "Ok":
                raise RuntimeError("Could not find a driving route between those two places.")
            pts = [(y, x) for x, y in r["routes"][0]["geometry"]["coordinates"]]
            # sample about every 1 km
            samp = [pts[0]]; acc = 0
            for p0, p1 in zip(pts, pts[1:]):
                acc += regionbuild.hav(p0[0], p0[1], p1[0], p1[1])
                if acc >= 1.0:
                    samp.append(p1); acc = 0
            samp.append(pts[-1])
            buckets = {}
            for q in samp:
                buckets.setdefault((int(q[0] / 0.15), int(q[1] / 0.15)), []).append(q)
            cells_all = list(get_state()["cells"].keys())
            width = 12.0
            while True:
                sel = []
                for (i, j) in cells_all:
                    la, lo = (i + .5) * .02, (j + .5) * .02
                    bi, bj = int(la / 0.15), int(lo / 0.15)
                    near = False
                    for di in (-1, 0, 1):
                        for dj in (-1, 0, 1):
                            for q in buckets.get((bi + di, bj + dj), ()):
                                if regionbuild.hav(la, lo, q[0], q[1]) <= width:
                                    near = True; break
                            if near: break
                        if near: break
                    if near:
                        sel.append((i, j))
                if len(sel) <= 2200 or width <= 5:
                    break
                width -= 2
            if len(sel) < 3:
                raise RuntimeError("Warroom has too few cells along that route.")
            unc = []
            room = 2200 - len(sel)
            if room > 0:
                have = set(cells_all); best = {}
                for q in samp:
                    ci, cj = int(q[0] / 0.02), int(q[1] / 0.02)
                    for di in range(-3, 4):
                        for dj in range(-3, 4):
                            k = (ci + di, cj + dj)
                            if k in have:
                                continue
                            d = regionbuild.hav((k[0] + .5) * .02, (k[1] + .5) * .02, q[0], q[1])
                            if d <= 5 and d < best.get(k, 99):
                                best[k] = d
                unc = [k for k, _ in sorted(best.items(), key=lambda t: t[1])[:room]]
            simp = regionbuild.enc(regionbuild.rdp(pts, 60))
            meta = {"kind": "corridor", "width": width, "alat": a[0], "alon": a[1], "aname": a[2], "blat": b[0], "blon": b[1], "bname": b[2]}
            regionbuild.build(rid, nm[:60], (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, None, sel,
                              [("A", a[0], a[1]), ("B", b[0], b[1])],
                              os.path.join(DATA, "regions", rid + ".json"), progress,
                              meta=meta, tail={"route": simp}, n_hubs=14, workers=4, uncharted=unc)
        except Exception as e:
            traceback.print_exc()
            BUILD["error"] = str(e)
        finally:
            BUILD["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return True


def uncharted_near(lat, lon, radius, have, cap):
    """Grid cells within radius km of a point that warroom has no cell for, nearest first."""
    out = []
    di = int(radius / 2.2) + 2
    ci, cj = int(lat / 0.02), int(lon / 0.02)
    for i in range(ci - di, ci + di + 1):
        for j in range(cj - int(di / max(math.cos(math.radians(lat)), 0.3)) - 1, cj + int(di / max(math.cos(math.radians(lat)), 0.3)) + 2):
            if (i, j) in have:
                continue
            d = regionbuild.hav(lat, lon, (i + .5) * .02, (j + .5) * .02)
            if d <= radius:
                out.append((d, i, j))
    out.sort()
    return [(i, j) for _, i, j in out[:max(cap, 0)]]


BUILD = {"running": False, "id": None, "name": None, "step": "", "done": 0, "total": 0, "error": None}
BLOCK = threading.Lock()


def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:40] or "area"


def start_build(name, lat, lon, radius):
    with BLOCK:
        if BUILD["running"]:
            return False
        rid = "%s-%d-%d" % (slug(name), round(abs(lat) * 10), round(abs(lon) * 10))
        BUILD.update(running=True, id=rid, name=name, step="Starting", done=0, total=0, error=None)

    def progress(step, done, total):
        BUILD.update(step=step, done=done, total=total)

    def work():
        try:
            have = set(get_state()["cells"].keys())
            cells = list(have)
            near = sum(1 for (i, j) in cells if regionbuild.hav(lat, lon, (i + .5) * .02, (j + .5) * .02) <= radius)
            unc = uncharted_near(lat, lon, radius, have, 1500 - near)
            regionbuild.build(rid, name, lat, lon, radius, cells, [], os.path.join(DATA, "regions", rid + ".json"), progress, uncharted=unc)
        except Exception as e:
            traceback.print_exc()
            BUILD["error"] = str(e)
        finally:
            BUILD["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return True


def geocode(q):
    u = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + urllib.parse.quote(q)
    req = urllib.request.Request(u, headers={"User-Agent": "wardrive-route-planner/1.0 (personal use)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)
    if not res:
        return None
    return float(res[0]["lat"]), float(res[0]["lon"]), res[0]["display_name"].split(",")[0].strip()[:60]


# ---------- heat file ----------
HEAT_FILE = os.path.join(DATA, "wigle_cell_heat.csv")
_heat = {"mtime": None, "rows": []}


def heat_rows():
    if not os.path.exists(HEAT_FILE):
        return None, []
    mt = os.path.getmtime(HEAT_FILE)
    if _heat["mtime"] != mt:
        rows = []
        with open(HEAT_FILE, newline="") as f:
            rd = csv.DictReader(f)
            for r in rd:
                try:
                    rows.append([int(r["i"]), int(r["j"]), int(float(r.get("wifi_nets") or 0)), int(float(r.get("ble_nets") or 0)), int(float(r.get("nets") or 0))])
                except Exception:
                    continue
        _heat.update(mtime=mt, rows=rows)
    return _heat["mtime"], _heat["rows"]


# ---------- recent WiGLE density file ----------
DENS_FILE = os.path.join(DATA, "wigle_density.csv")
_dens = {"mtime": None, "rows": []}


def parse_density(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                rows.append([int(float(r["i"])), int(float(r["j"])), round(max(0.0, float(r["wifi_recent"])), 2)])
            except Exception:
                continue
    return rows


def density_rows():
    if not os.path.exists(DENS_FILE):
        return None, []
    mt = os.path.getmtime(DENS_FILE)
    if _dens["mtime"] != mt:
        _dens.update(mtime=mt, rows=parse_density(DENS_FILE))
    return _dens["mtime"], _dens["rows"]


# ---------- auth ----------
FAILS = {}


def make_cookie():
    exp = str(int(time.time()) + 30 * 86400)
    return exp + "." + hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()


def valid_cookie(c):
    try:
        exp, sig = c.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest())
    except Exception:
        return False


LOGIN = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in</title>
<style>body{font-family:system-ui,sans-serif;background:#f3f4f1;color:#172126;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
@media(prefers-color-scheme:dark){body{background:#101619;color:#e6ecee}input{background:#192226;color:#e6ecee}}
form{width:min(340px,90vw);display:flex;flex-direction:column;gap:12px}h1{font-size:20px;margin:0}
input,button{font:inherit;padding:12px;border-radius:8px;border:1px solid #8a9aa0}button{background:#0d6b78;color:#fff;border-color:#0d6b78;font-weight:600}.e{color:#c4510f;font-size:14px}</style></head>
<body><form method="post" action="/login"><h1>Wardrive Route Planner</h1>%s<input name="username" value="planner" autocomplete="username" style="position:absolute;left:-9999px" tabindex="-1" aria-hidden="true"><label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" autofocus required><button type="submit">Sign in</button></form></body></html>"""


class H(BaseHTTPRequestHandler):
    server_version = "rp/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def ip(self):
        return self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]

    def authed(self):
        m = re.search(r"rp_session=([^;]+)", self.headers.get("Cookie", ""))
        return bool(m and valid_cookie(m.group(1)))

    def send(self, code, body, ctype="application/json", extra=None, gz=True):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, separators=(",", ":")).encode()
        elif isinstance(body, str):
            body = body.encode()
        headers = {"Content-Type": ctype, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if gz and len(body) > 1400 and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, 5); headers["Content-Encoding"] = "gzip"
        headers["Content-Length"] = str(len(body))
        self.send_response(code)
        for k, v in {**headers, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, to, extra=None):
        self.send_response(303); self.send_header("Location", to)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0"); self.end_headers()

    def body(self, limit=60_000_000):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("Body too large")
        return self.rfile.read(n)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); path = u.path
        try:
            if path == "/healthz":
                return self.send(200, "ok", "text/plain")
            if path == "/favicon.ico":
                self.send_response(204); self.send_header("Content-Length", "0"); self.end_headers(); return
            if path == "/login":
                return self.send(200, LOGIN % "", "text/html; charset=utf-8")
            if not self.authed():
                if path.startswith("/api/"):
                    return self.send(401, {"error": "Sign in required"})
                return self.redirect("/login")
            if path in ("/", "/index.html"):
                return self.send(200, open(os.path.join(HERE, "static", "index.html"), "rb").read(), "text/html; charset=utf-8")
            if path == "/api/regions":
                return self.send(200, {"regions": list_regions(), "build": BUILD, "asof": (STATE["asof"] or "")})
            if path.startswith("/api/region/"):
                p = region_payload(path.rsplit("/", 1)[1])
                return self.send(200, p) if p else self.send(404, {"error": "Unknown region"})
            if path == "/api/build/status":
                return self.send(200, BUILD)
            if path == "/api/geocode":
                q = (urllib.parse.parse_qs(u.query).get("q") or [""])[0][:140].strip()
                if len(q) < 3:
                    return self.send(400, {"error": "Type at least 3 characters."})
                return self.send(200, {"results": search_places(q)})
            if path == "/api/locate":
                qs = urllib.parse.parse_qs(u.query)
                lat, lon = float(qs["lat"][0]), float(qs["lon"][0])
                cov = covering(lat, lon)
                return self.send(200, {"covered": cov, "best": cov[0]["id"] if cov else None})
            if path == "/api/heat":
                mt, rows = heat_rows()
                return self.send(200, {"at": mt and time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mt)), "rows": rows})
            if path == "/api/density":
                mt, rows = density_rows()
                return self.send(200, {"at": mt and time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mt)), "rows": rows})
            return self.send(404, {"error": "Not found"})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": str(e)})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path); path = u.path
        try:
            if path == "/login":
                ip = self.ip(); now = time.time()
                n, t0 = FAILS.get(ip, (0, now))
                if now - t0 > 900:
                    n, t0 = 0, now
                if n >= 10:
                    return self.send(429, LOGIN % '<p class="e">Too many attempts. Try again in 15 minutes.</p>', "text/html; charset=utf-8")
                form = urllib.parse.parse_qs(self.body(10000).decode("utf-8", "ignore"))
                pw = (form.get("password") or [""])[0]
                if hmac.compare_digest(pw.encode(), PASSWORD.encode()):
                    FAILS.pop(ip, None)
                    secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
                    return self.redirect("/", {"Set-Cookie": "rp_session=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (make_cookie(), 30 * 86400, secure)})
                FAILS[ip] = (n + 1, t0); time.sleep(1)
                return self.send(401, LOGIN % '<p class="e">Wrong password.</p>', "text/html; charset=utf-8")
            if not self.authed():
                return self.send(401, {"error": "Sign in required"})
            if self.headers.get("X-RP") != "1":
                return self.send(403, {"error": "Missing header"})
            if path == "/api/build":
                d = json.loads(self.body(10000) or b"{}")
                if d.get("corridor"):
                    c = d["corridor"]
                    a = (float(c["a"][0]), float(c["a"][1]), str(c["a"][2] if len(c["a"]) > 2 else "Start")[:30])
                    b = (float(c["b"][0]), float(c["b"][1]), str(c["b"][2] if len(c["b"]) > 2 else "End")[:30])
                    for la_, lo_ in ((a[0], a[1]), (b[0], b[1])):
                        if not (-80 < la_ < 80 and -180 <= lo_ <= 180):
                            return self.send(400, {"error": "Bad coordinates"})
                    ok = start_corridor(a, b)
                    return self.send(200 if ok else 409, {"started": ok, "build": BUILD})
                if d.get("query") and d.get("lat") is None:
                    g = geocode(str(d["query"])[:120])
                    if not g:
                        return self.send(404, {"error": "Could not find that place. Try a city and state."})
                    d["lat"], d["lon"], d["name"] = g
                lat, lon = float(d["lat"]), float(d["lon"])
                if not (-80 < lat < 80 and -180 <= lon <= 180):
                    return self.send(400, {"error": "Bad coordinates"})
                name = str(d.get("name") or "").strip()[:60]
                if not name or name == "New area":
                    name = reverse_name(lat, lon)
                radius = max(10, min(50, float(d.get("radius") or 40)))
                ok = start_build(name, lat, lon, radius)
                return self.send(200 if ok else 409, {"started": ok, "build": BUILD})
            if path == "/api/heat":
                raw = self.body()
                head = raw[:2000].decode("utf-8", "ignore").splitlines()[0].lower() if raw else ""
                if not all(k in head.split(",") for k in ("i", "j", "wifi_nets")):
                    return self.send(400, {"error": "That is not wigle_cell_heat.csv (missing i, j, wifi_nets columns)."})
                tmp = HEAT_FILE + ".tmp"
                open(tmp, "wb").write(raw); os.replace(tmp, HEAT_FILE)
                mt, rows = heat_rows()
                return self.send(200, {"cells": len(rows)})
            if path == "/api/density":
                raw = self.body()
                lines = raw[:2000].decode("utf-8-sig", "ignore").splitlines() if raw else []
                cols = [c.strip() for c in (lines[0].lower().split(",") if lines else [])]
                if not all(k in cols for k in ("i", "j", "wifi_recent")):
                    return self.send(400, {"error": "That is not wigle_density.csv (missing i, j, wifi_recent columns)."})
                tmp = DENS_FILE + ".tmp"
                open(tmp, "wb").write(raw)
                rows = parse_density(tmp)
                if not rows:
                    os.remove(tmp)
                    return self.send(400, {"error": "wigle_density.csv has no usable rows."})
                os.replace(tmp, DENS_FILE)
                _dens["mtime"] = None
                mt, rows = density_rows()
                return self.send(200, {"cells": len(rows)})
            return self.send(404, {"error": "Not found"})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": str(e)})


if __name__ == "__main__":
    threading.Thread(target=poll_loop, daemon=True).start()
    print("Route planner listening on", PORT, flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
