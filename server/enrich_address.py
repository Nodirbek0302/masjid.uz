"""Fills missing addresses of imported mosques from OpenStreetMap data (ODbL), offline after one download.

For every imported mosque that has no address and was never edited by a person:
  1. nearest OSM object carrying addr:street + addr:housenumber within --addr-radius metres  -> "Street 166"
  2. else nearest named road within --road-radius metres                                    -> "Street"
Both are neighbours' data, so the result is approximate: the history note says so. Human edits always win.

  python server/enrich_address.py --download            # Geofabrik's Uzbekistan extract (~125 MB) into ./osm-cache, once
  python server/enrich_address.py --apply [--dry-run]   # read it locally, match and write   (needs: pip install osmium)

The public Overpass service is for light queries, so the bulk data comes from Geofabrik's regular extract instead.
"""
import argparse
import math
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402

CACHE = Path(os.environ.get("OSM_CACHE", Path(__file__).resolve().parent.parent / "osm-cache"))
PBF_URL = os.environ.get("OSM_PBF_URL", "https://download.geofabrik.de/asia/uzbekistan-latest.osm.pbf")
PBF = CACHE / "uzbekistan.osm.pbf"
UA = "masjid-uz-enrich/0.1 (%s)" % os.environ.get("OSM_CONTACT", "no contact set")
ROAD_TYPES = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "pedestrian", "road"}
CELL = 0.0006          # ~65 m grid for neighbour lookup


def download():
    CACHE.mkdir(exist_ok=True)
    subprocess.run(["curl", "-fSL", "-A", UA, "-o", str(PBF), PBF_URL], check=True)     # curl: Python 3.9 on macOS cannot do this TLS handshake
    print("saved %s (%.1f MB)" % (PBF, PBF.stat().st_size / 1e6))


def mpd(lat):                                       # metres per degree (lat, lon)
    return 111320.0, 111320.0 * math.cos(math.radians(lat))


def cell(lat, lon):
    return int(lat / CELL), int(lon / CELL)


def build_indexes(path):
    """One pass over the extract -> (address index, road index). Needs pyosmium."""
    import osmium

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.addr, self.roads = {}, {}

        def _addr(self, lat, lon, t):
            st, num = (t.get("addr:street") or "").strip(), (t.get("addr:housenumber") or "").strip()
            if st and num:
                self.addr.setdefault(cell(lat, lon), []).append((lat, lon, st, num, (t.get("addr:city") or "").strip()))

        def node(self, n):
            if n.location.valid() and "addr:housenumber" in n.tags:
                self._addr(n.location.lat, n.location.lon, n.tags)

        def way(self, w):
            t = w.tags
            pts = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
            if len(pts) < 2:
                return
            if "addr:housenumber" in t:
                self._addr(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), t)
            name = (t.get("name") or "").strip()
            if name and t.get("highway") in ROAD_TYPES:
                seg = (name, pts)
                for c in {cell(la, lo) for la, lo in pts}:       # registered in every cell any of its vertices touches
                    self.roads.setdefault(c, []).append(seg)

    h = H()
    h.apply_file(str(path), locations=True)
    return h.addr, h.roads


def near_cells(lat, lon, r):
    k = int(r / 65.0) + 1
    c0, c1 = cell(lat, lon)
    return [(c0 + i, c1 + j) for i in range(-k, k + 1) for j in range(-k, k + 1)]


def nearest_addr(idx, lat, lon, r):
    my, mx = mpd(lat)
    best = None
    for c in near_cells(lat, lon, r):
        for (la, lo, street, num, city) in idx.get(c, ()):
            d = math.hypot((la - lat) * my, (lo - lon) * mx)
            if d <= r and (best is None or d < best[0]):
                best = (d, street, num, city)
    return best


def seg_dist(lat, lon, a, b):
    my, mx = mpd(lat)
    ax, ay, bx, by = (a[1] - lon) * mx, (a[0] - lat) * my, (b[1] - lon) * mx, (b[0] - lat) * my
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + t * dx, ay + t * dy)


def nearest_road(idx, lat, lon, r):
    best, seen = None, set()
    for c in near_cells(lat, lon, r):
        for name, pts in idx.get(c, ()):
            if id(pts) in seen:
                continue
            seen.add(id(pts))
            for a, b in zip(pts, pts[1:]):
                d = seg_dist(lat, lon, a, b)
                if d <= r and (best is None or d < best[0]):
                    best = (d, name)
    return best


def run(addr_idx, road_idx, cfg, addr_radius=25.0, road_radius=60.0, dry=False, store=None):
    store = store or app.Store(cfg)
    n_addr = n_road = n_none = 0
    with store.pool.connection() as c:
        cur = c.cursor()
        cur.execute("SELECT m.* FROM mosques m WHERE m.origin='imported' AND m.address IS NULL AND m.status<>'archived' "
                    "AND NOT EXISTS (SELECT 1 FROM mosque_history h WHERE h.mosque_id=m.id AND h.actor IS DISTINCT FROM 'import:osm')")
        rows = cur.fetchall()
        for r in rows:
            text = note = None
            a = nearest_addr(addr_idx, r["lat"], r["lon"], addr_radius)
            if a:
                text = ("%s %s" % (a[1], a[2])) + (", " + a[3] if a[3] else "")
                note = "Address from the nearest OpenStreetMap address object (%.0f m): approximate" % a[0]
                n_addr += 1
            else:
                rd = nearest_road(road_idx, r["lat"], r["lon"], road_radius)
                if rd:
                    text, note = rd[1], "Street from the nearest named OpenStreetMap road (%.0f m): approximate" % rd[0]
                    n_road += 1
            if not text:
                n_none += 1
                continue
            text = app.clean_text(text[:300], "address", 300)
            if dry or not text:
                continue
            v = r["version"] + 1
            cur.execute("UPDATE mosques SET address=%s, version=%s, updated_at=now() WHERE id=%s", (text, v, r["id"]))
            store._history(cur, str(r["id"]), v, "osm_sync", "import:osm", {"address": [None, text]}, note)
    summary = {"considered": len(rows), "street_and_number": n_addr, "street_only": n_road, "still_without": n_none}
    print(" ".join("%s=%d" % kv for kv in summary.items()) + (" (dry run)" if dry else ""))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--addr-radius", type=float, default=25.0)
    ap.add_argument("--road-radius", type=float, default=60.0)
    a = ap.parse_args()
    if not (a.download or a.apply):
        ap.error("--download or --apply required")
    if a.download:
        download()
    if a.apply:
        app.load_dotenv()
        addr_idx, road_idx = build_indexes(PBF)
        print("indexed: %d address cells, %d road cells" % (len(addr_idx), len(road_idx)))
        run(addr_idx, road_idx, app.Config(), a.addr_radius, a.road_radius, a.dry_run)


if __name__ == "__main__":
    main()
