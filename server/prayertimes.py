"""Daily prayer times from the sun's position: the same algorithm as web/prayer.js (kept identical; tests compare the two).
Used by the bot's reminders. Approximate: not the official calendar. Uzbekistan is UTC+5 all year (no daylight saving)."""
import math
import os

TZ = 5
# (latitude, longitude) of the 14 regions' centres; the names live in the bot's texts (same order as web/app.js REGIONS)
REGIONS = [(41.3111, 69.2797), (41.0167, 69.35), (40.7821, 72.3442), (39.7747, 64.4286), (40.3864, 71.7864), (40.1158, 67.8422), (41.55, 60.6333),
           (41.0011, 71.6683), (40.0844, 65.3792), (38.8606, 65.7891), (42.4531, 59.6103), (39.6542, 66.9597), (40.4897, 68.7842), (37.2242, 67.2783)]
PARAMS = {"fajr": float(os.environ.get("PRAYER_FAJR_ANGLE", "15")), "isha": float(os.environ.get("PRAYER_ISHA_ANGLE", "15")),
          "asr": int(os.environ.get("PRAYER_ASR_FACTOR", "2")), "dhuhr_min": int(os.environ.get("PRAYER_DHUHR_OFFSET_MIN", "5"))}
R = math.pi / 180


def _sin(d): return math.sin(d * R)
def _cos(d): return math.cos(d * R)
def _tan(d): return math.tan(d * R)
def _acos(x): return math.acos(max(-1.0, min(1.0, x))) / R
def _fix(v, n): return v - n * math.floor(v / n)


def _julian(y, m, d):
    if m <= 2:
        y -= 1
        m += 12
    a = math.floor(y / 100)
    b = 2 - a + math.floor(a / 4)
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + b - 1524.5


def _sun(jd):
    D = jd - 2451545.0
    g = _fix(357.529 + 0.98560028 * D, 360)
    q = _fix(280.459 + 0.98564736 * D, 360)
    L = _fix(q + 1.915 * _sin(g) + 0.020 * _sin(2 * g), 360)
    e = 23.439 - 0.00000036 * D
    ra = math.atan2(_cos(e) * _sin(L), _cos(L)) / R / 15
    return math.asin(_sin(e) * _sin(L)) / R, q / 15 - _fix(ra, 24)


def times(year, month, day, lat, lon, tz=TZ, params=None):
    """-> {"fajr","sunrise","dhuhr","asr","maghrib","isha"}: hours of the local day (floats)."""
    p = dict(PARAMS, **(params or {}))
    decl, eqt = _sun(_julian(year, month, day) + 0.5 - lon / 360)
    noon = _fix(12 - eqt, 24)

    def ang(a, ccw):
        t = _acos((-_sin(a) - _sin(decl) * _sin(lat)) / (_cos(decl) * _cos(lat))) / 15
        return noon + (-t if ccw else t)
    asr_angle = -math.atan(1 / (p["asr"] + _tan(abs(lat - decl)))) / R
    asr = noon + _acos((-_sin(asr_angle) - _sin(decl) * _sin(lat)) / (_cos(decl) * _cos(lat))) / 15
    out = {"fajr": ang(p["fajr"], True), "sunrise": ang(0.833, True), "dhuhr": noon + p["dhuhr_min"] / 60.0, "asr": asr,
           "maghrib": ang(0.833, False), "isha": ang(p["isha"], False)}
    shift = tz - lon / 15
    return {k: _fix(v + shift, 24) for k, v in out.items()}


def hm(h):
    t = int(round(h * 60))
    return "%02d:%02d" % ((t // 60) % 24, t % 60)
