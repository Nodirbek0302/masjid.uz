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


def _times_in(line):
    """HH:MM values in a line. OCR confuses O/0 and l/1: letters are repaired only inside tokens shaped like a time."""
    out = []
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


def _minutes(t):
    return int(t[:2]) * 60 + int(t[3:])


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
    complete = all(k in found for k in ORDER)
    if complete:
        mins = [_minutes(found[k]) for k in ORDER]
        if mins != sorted(mins) or len(set(mins)) != 5:
            warnings.append("not_in_order")
    bad = {"not_in_order", "too_many_times", "two_columns"} & set(warnings)
    return {"times": {k: found[k] for k in ORDER if k in found}, "alt": alt, "complete": complete and not bad, "warnings": warnings}
