"""Reads the five daily congregation times out of free text (OCR output of a photographed timetable, or typed by hand).

Strategy: (1) lines that name a prayer (Bomdod/Fajr/Фаджр/Бомдод..., Peshin/Dhuhr/Зухр/Пешин..., Asr, Shom/Maghrib/Магриб/Шом,
Xufton/Isha/Иша/Хуфтон) give that prayer's time; (2) otherwise the HH:MM values are taken in reading order (5 values, or 6 when the
sunrise time is included, which is then dropped). Anything doubtful is flagged so the user confirms or corrects it: a wrong time
must never be saved silently.
"""
import re

ORDER = ("fajr", "dhuhr", "asr", "maghrib", "isha")
LABELS = {
    "fajr": ("bomdod", "bomdad", "fajr", "fajir", "фаджр", "фаджир", "бомдод", "бомдад", "тонг", "утренний", "subh"),
    "sunrise": ("quyosh", "kuyosh", "sunrise", "восход", "қуёш", "куёш", "shuruq", "voshod"),
    "dhuhr": ("peshin", "pеshin", "dhuhr", "zuhr", "zohr", "duhr", "зухр", "зуҳр", "пешин", "полдень", "обед"),
    "asr": ("asr", "аср", "наср", "насри"),
    "maghrib": ("shom", "maghrib", "magrib", "magreb", "магриб", "шом", "закат", "вечерний"),
    "isha": ("xufton", "hufton", "xuftan", "isha", "иша", "хуфтон", "ночной", "khufton"),
}
_TIME = re.compile(r"(?<![\d:])([01]?\d|2[0-3])\s*[:.·\-]\s*([0-5]\d)(?![\d])")


_LOOSE = re.compile(r"(?<![\w])([\dOolI|]{1,2})\s*[:.·\-]\s*([\dOolI|]{2})(?![\w])")
_TR = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"})


_DATE = re.compile(r"(?<!\d)\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}(?!\d)")


_AMPM = re.compile(r"(?<!\d)(\d{1,2})\s*[:.]\s*(\d{2})\s*([AaPp])\.?\s?[Mm]\b\.?")


def _to24(h, ap):
    """12-hour clock with an explicit marker -> 24-hour hour."""
    ap = ap.lower()
    if ap == "p" and h < 12:
        return h + 12
    if ap == "a" and h == 12:
        return 0
    return h


def _times_in(line):
    """HH:MM values in a line. OCR confuses O/0 and l/1: letters are repaired only inside tokens shaped like a time.
    Dates such as 23.10.2026 (common on boards) are removed first so that "23.10" is not read as 23:10."""
    out = []
    line = _AMPM.sub(lambda mo: "%02d:%s" % (_to24(int(mo.group(1)), mo.group(3)), mo.group(2)), _DATE.sub(" ", line))   # "4:00 PM" -> "16:00"
    for h, m in _LOOSE.findall(line):
        h, m = h.translate(_TR), m.translate(_TR)
        if h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59:
            out.append("%02d:%02d" % (int(h), int(m)))
    return out


def _label_of(line):
    low = line.lower()
    best = None
    for key, words in LABELS.items():
        for w in words:
            i = low.find(w)
            if i >= 0 and (best is None or i < best[0]):
                best = (i, key)
    return best[1] if best else None


# Wide windows (minutes after midnight) that hold for Uzbekistan all year; anything outside is an OCR slip or a typo, never saved silently.
PLAUSIBLE = {"fajr": (150, 480), "dhuhr": (690, 810), "asr": (900, 1110), "maghrib": (990, 1290), "isha": (1060, 1410)}


def _minutes(t):
    return int(t[:2]) * 60 + int(t[3:])


def _cands(k, t):
    """Readings of one value for prayer k inside its window of the day, as (value, kind): as written, +12 h (a 12-hour PM time), +10 h
    (OCR lost the leading "1": 6:00 -> 16:00), -12 h."""
    h, mi = int(t[:2]), t[3:]
    lo, hi = PLAUSIBLE[k]
    out = []
    for cand, kind in ((h, ""), (h + 12, "+12"), (h + 10, "+10"), (h - 12, "-12")):
        v = "%02d:%s" % (cand, mi)
        if 0 <= cand < 24 and lo <= cand * 60 + int(mi) <= hi and v not in [c[0] for c in out]:
            out.append((v, kind))
    return out


# Natural gaps (minutes) between neighbouring prayers in Uzbekistan all year (Hanafi Asr). Used to choose between readings that all fit
# the windows and the increasing order, e.g. Asr "6:00": 18:00 when Maghrib is 20:00, but 16:00 (OCR lost the "1") when Maghrib is 17:35.
GAPS = {("fajr", "dhuhr"): (300, 560), ("dhuhr", "asr"): (150, 360), ("asr", "maghrib"): (55, 200), ("maghrib", "isha"): (45, 150)}


def resolve_12h(found):
    """Boards and speakers often give 12-hour times without AM/PM ("Asr 4:00", "Isha 7:15"), and OCR sometimes drops a leading "1".
    For every prayer the readings inside its window of the day are candidates; the chosen combination keeps the prayers in increasing
    order, has the fewest unnatural gaps between neighbours, uses one kind of fix throughout (all "+12 h" or all "lost 1") and then
    prefers the most natural reading. 4:00 -> 16:00 for Asr, 7:15 -> 19:15 for Isha, 1:15 -> 13:15 for Dhuhr, 5:20 stays for Fajr;
    Asr 6:00 -> 18:00 or 16:00 depending on what Maghrib says. Returns (times, changed_prayers)."""
    import itertools
    ks = [k for k in ORDER if k in found]
    options = [_cands(k, found[k]) or [(found[k], "?")] for k in ks]
    best, best_key = None, None
    for idx in itertools.product(*[range(len(o)) for o in options]):
        picks = [options[i][j] for i, j in enumerate(idx)]
        mins = [_minutes(v) for v, _ in picks]
        if any(b <= a for a, b in zip(mins, mins[1:])):
            continue
        bad = 0
        for (k1, k2), (lo, hi) in GAPS.items():
            if k1 in ks and k2 in ks:
                bad += not lo <= mins[ks.index(k2)] - mins[ks.index(k1)] <= hi
        kinds = len({kd for (v, kd), k in zip(picks, ks) if kd and v != found[k]})
        key = (bad, kinds, sum(idx), idx)
        if best_key is None or key < best_key:
            best, best_key = [v for v, _ in picks], key
    if best is None:                                                  # no increasing combination: first readings, flagged later
        best = [o[0][0] for o in options]
    out, changed = dict(found), []
    for k, c in zip(ks, best):
        if c != found[k]:
            out[k] = c
            changed.append(k)
    return out, changed


def _repair(found):
    """If exactly the afternoon/evening times are out of order, try the usual OCR slips (lost leading 1: 6:00 -> 16:00, or 12h shift).
    Returns a repaired dict only when the result is strictly increasing, else None."""
    out = dict(found)
    prev = _minutes(out["fajr"])
    for k in ORDER[1:]:
        m = _minutes(out[k])
        if m <= prev:
            h, mi = int(out[k][:2]), out[k][3:]
            cands = [h + 10, h + 12]
            ok = [c for c in cands if c < 24 and prev < c * 60 + int(mi)]
            if not ok:
                return None
            out[k] = "%02d:%s" % (ok[0], mi)
            m = _minutes(out[k])
        prev = m
    return out


def _row5(ts):
    """One row of times -> the five prayers (drops the sunrise column when six values are given), or None."""
    if len(ts) == 5:
        return dict(zip(ORDER, ts))
    if len(ts) == 6 and _minutes(ts[0]) < _minutes(ts[1]) < _minutes(ts[2]):
        return dict(zip(ORDER, ts[:1] + ts[2:]))
    return None


def parse(text):
    """-> {"times": {prayer: "HH:MM"}, "alt": {...}|None, "complete": bool, "warnings": [...]}
    `alt` is the second column when the board has two (azon + jamoat): the caller lets the user choose."""
    lines = [ln.strip() for ln in (text or "").replace("\r", "\n").split("\n") if ln.strip()]
    warnings, alt = [], None
    per = {}                                                    # prayer -> all times on its line(s)
    for ln in lines:
        ts, lab = _times_in(ln), _label_of(ln)
        if lab and ts and lab != "sunrise":
            per.setdefault(lab, ts)
    found = {k: v[0] for k, v in per.items()}
    rows = [_row5(r) for r in (_times_in(ln) for ln in lines) if len(r) >= 5]            # a long board: whole rows of times
    rows = [r for r in rows if r]
    if len(rows) >= 2 and not per:
        found, alt = rows[0], rows[1]                                                   # e.g. an "azon" row and a "jamoat" row: the user chooses
        warnings.append("two_columns")
    if len(per) >= 4 and all(len(v) >= 2 for v in per.values()):          # two time columns on a labelled board
        alt = {k: v[1] for k, v in per.items()}
        warnings.append("two_columns")
    if len(found) < 5:
        allt = [t for ln in lines for t in _times_in(ln)]
        if len(allt) == 5:
            found = dict(zip(ORDER, allt))
        elif len(allt) == 6 and _minutes(allt[0]) < _minutes(allt[1]) < _minutes(allt[2]):
            found = dict(zip(ORDER, allt[:1] + allt[2:]))                  # drop the sunrise column
            warnings.append("sunrise_dropped")
        elif len(allt) > 6:
            warnings.append("too_many_times")
        elif not found:
            found = dict(zip(ORDER, allt))
    found, changed = resolve_12h(found)
    if changed:
        warnings.append("ampm_resolved")
    complete = all(k in found for k in ORDER)
    if complete:
        mins = [_minutes(found[k]) for k in ORDER]
        if mins != sorted(mins) or len(set(mins)) != 5:
            fixed = _repair(found)
            if fixed:                                   # e.g. OCR dropped the leading "1" of "16:00"; the user still confirms
                found = fixed
                warnings.append("repaired")
            else:
                warnings.append("not_in_order")
    if complete and "not_in_order" not in warnings and any(not PLAUSIBLE[k][0] <= _minutes(found[k]) <= PLAUSIBLE[k][1] for k in ORDER):
        warnings.append("implausible")
    bad = {"not_in_order", "too_many_times", "two_columns", "implausible"} & set(warnings)
    if alt:
        alt, _ = resolve_12h(alt)
    return {"times": {k: found[k] for k in ORDER if k in found}, "alt": alt, "complete": complete and not bad, "warnings": warnings}


def best_of(texts):
    """Combine several OCR readings of one photo.
    1) any reading that shows two time columns wins (another pass may have silently dropped one column);
    2) otherwise the complete reading most passes agree on; 3) otherwise the reading with the most recognised times."""
    parsed = [parse(x) for x in texts]
    if not parsed:
        return {"times": {}, "alt": None, "complete": False, "warnings": []}
    two = [r for r in parsed if r["alt"]]
    if two:
        return max(two, key=lambda r: len(r["times"]))
    comp = [r for r in parsed if r["complete"]]
    if comp:
        votes = {}
        for r in comp:
            votes.setdefault(tuple(sorted(r["times"].items())), []).append(r)
        return max(votes.values(), key=len)[0]
    return max(parsed, key=lambda r: len(r["times"]))
