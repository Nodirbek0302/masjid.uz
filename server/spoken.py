"""Turns the transcript of a voice message into the five congregation times.

People say times in words ("Bomdod besh yigirma, Peshin o'n ikki o'ttiz ..." / "Фаджр пять двадцать ..." / "Fajr five twenty ..."),
and speech recognisers write them as words, digits or "5:20". This module understands all of these, in Uzbek (Latin and Cyrillic),
Russian and English, and hands clean text ("Bomdod 05:20 ...") to timetable.parse. The user always confirms the result.
"""
import re

import timetable
import translit


def fold(w):
    """lower-case, Cyrillic -> Latin, apostrophes removed: o'n = ўн = on, to'rt = тўрт = tort."""
    return translit.norm_apos(translit.to_latin(w.lower())).replace("'", "")


def _dict(pairs):
    out = {}
    for words, val in pairs:
        for w in words.split():
            out[fold(w)] = val
    return out


UNITS = _dict([
    ("nol nul ноль нул нол zero oh", 0),
    ("bir один одна one", 1), ("ikki два две two", 2), ("uch уч три three", 3), ("to'rt тўрт четыре four", 4), ("besh беш пять five", 5),
    ("olti олти шесть six", 6), ("yetti етти семь seven", 7), ("sakkiz саккиз восемь eight", 8), ("to'qqiz тўққиз девять nine", 9),
])
TEENS = _dict([
    ("десять ten", 10), ("одиннадцать eleven", 11), ("двенадцать twelve", 12), ("тринадцать thirteen", 13), ("четырнадцать fourteen", 14),
    ("пятнадцать fifteen", 15), ("шестнадцать sixteen", 16), ("семнадцать seventeen", 17), ("восемнадцать eighteen", 18),
    ("девятнадцать nineteen", 19),
])
TENS = _dict([
    ("o'n ўн", 10), ("yigirma йигирма двадцать twenty", 20), ("o'ttiz ўттиз тридцать thirty", 30), ("qirq қирқ сорок forty", 40),
    ("ellik эллик пятьдесят fifty", 50),
])
ZERO_MINUTES = {fold(w) for w in "hundred ровно sharp roвно".split()}
FILLERS = {fold(w) for w in "va soat soatu daqiqa daqiqada minut minutda и час часов часа минут минуты o'clock oclock and hours hour minutes minute am pm".split()}
EXTRA_LABELS = {"fajr": ("bamdod", "бамдод", "bomdot", "fajar"), "sunrise": ("quyoş",), "dhuhr": ("pishin", "пешин", "peshn", "пешим", "dhur", "zuhr"),
                "asr": ("asar", "аср", "asir"), "maghrib": ("shom", "шом", "magrib", "магриб"),
                "isha": ("xuftan", "хуфтан", "huften", "khuftan", "xufton", "хуфтон", "isha", "иша")}
CANON = {"fajr": "Bomdod", "sunrise": "Quyosh", "dhuhr": "Peshin", "asr": "Asr", "maghrib": "Shom", "isha": "Xufton"}
_LABEL_FOLDED = {}
for _k, _words in list(timetable.LABELS.items()) + list(EXTRA_LABELS.items()):
    for _w in _words:
        _LABEL_FOLDED[fold(_w)] = _k
AM_WORDS = {fold(w) for w in "am a.m ertalab tongda tong утра утром".split()}
PM_WORDS = {fold(w) for w in "pm p.m kechqurun kechasi kechki tushdan кечкурун дня днём вечера вечером ночи".split()}
_TOKEN = re.compile(r"\d{1,2}\s*[:.]\s*\d{2}|\d+|[^\W\d_]+(?:['ʻʼ‘’`´][^\W\d_]+)*", re.U)


def _label(tok):
    f = fold(tok)
    if f in _LABEL_FOLDED:
        return _LABEL_FOLDED[f]
    return next((k for w, k in _LABEL_FOLDED.items() if len(w) >= 4 and f.startswith(w)), None)


def _events(text):
    """-> list of ("label", key) | ("num", int, from_word) | ("time", "HH:MM") | ("minute0",)"""
    toks = _TOKEN.findall(text)
    ev = []
    for i, tok in enumerate(toks):
        if re.fullmatch(r"\d{1,2}\s*[:.]\s*\d{2}", tok):
            h, m = re.split(r"\s*[:.]\s*", tok)
            ev.append(("time", "%02d:%s" % (int(h), m)))
        elif tok.isdigit():
            ev.append(("num", int(tok), False))
        else:
            f = fold(tok)
            lab = _label(tok)
            if f == "chas" and ev and ev[-1][0] == "label":                   # Russian "час пятнадцать" = 1:15 (hour 1), not the filler "hour"
                ev.append(("num", 1, True))
            elif lab:
                ev.append(("label", lab))
            elif f in UNITS:
                ev.append(("num", UNITS[f], True))
            elif f in TEENS:
                ev.append(("num", TEENS[f], True))
            elif f in TENS:
                nxt = fold(toks[i + 1]) if i + 1 < len(toks) else ""
                if f == "on" and nxt not in UNITS and nxt not in TENS and not (ev and ev[-1][0] == "num"):   # a bare English "on" is not 10; "to'rt o'n" is 4:10
                    continue
                ev.append(("num", TENS[f], True))
            elif f in ZERO_MINUTES:
                ev.append(("minute0",))
            elif f in AM_WORDS:
                ev.append(("ampm", "a"))
            elif f in PM_WORDS:
                ev.append(("ampm", "p"))
    return ev


def _combine(ev):
    """Join spoken number words: tens + unit (twenty five, yigirma besh, o'n ikki), 0 + unit (nol besh)."""
    out = []
    for e in ev:
        if e[0] == "num" and e[2] and out and out[-1][0] == "num" and out[-1][2] and not out[-1][3]:
            prev = out[-1]
            if prev[1] in (10, 20, 30, 40, 50) and 1 <= e[1] <= 9:
                out[-1] = ("num", prev[1] + e[1], True, True)             # closed group: cannot absorb more
                continue
            if prev[1] == 0 and 1 <= e[1] <= 9:
                out[-1] = ("num", e[1], True, True)
                continue
        out.append(e + (False,) if e[0] == "num" else e)
    return out


def normalize(text):
    """Transcript -> text the timetable parser understands (one 'Name HH:MM' line per spoken prayer)."""
    ev = _combine(_events(text or ""))
    lines, free = [], []
    label, nums, mark = None, [], None

    def flush():
        nonlocal label, nums, mark
        if label and nums:
            h, m = (nums + [0])[:2]
            if mark:                                                            # "... pm" / "kechqurun" / "утра": explicit 12-hour marker
                h = timetable._to24(h, mark)
            if 0 <= h <= 23 and 0 <= m <= 59:
                lines.append("%s %02d:%02d" % (CANON[label], h, m))
        elif nums and not label:
            free.extend(nums)
        label, nums, mark = None, [], None

    for e in ev:
        if e[0] == "label":
            flush()
            label = e[1]
        elif e[0] == "time":
            nums.extend([int(e[1][:2]), int(e[1][3:])])
        elif e[0] == "num":
            nums.append(e[1])
        elif e[0] == "minute0" and len(nums) == 1:
            nums.append(0)
        elif e[0] == "ampm":
            mark = e[1]
        if label is None and len(nums) >= 2 and e[0] != "label":              # free numbers: collect hour/minute pairs
            free.extend(nums[:2]); nums = nums[2:]
    flush()
    if not lines and len(free) >= 10:                                           # no names spoken: five hour/minute pairs in order
        pairs = ["%02d:%02d" % (free[i], free[i + 1]) for i in range(0, 10, 2) if 0 <= free[i] <= 23 and 0 <= free[i + 1] <= 59]
        lines.append(" ".join(pairs))
    return "\n".join(lines)


def parse(transcript):
    """-> timetable.parse() result for the spoken text (digits and times written by the recogniser are understood too)."""
    r = timetable.parse(normalize(transcript))
    if r["complete"] or r["alt"]:
        return r
    raw = timetable.parse(transcript or "")                                     # the recogniser may already have written clean times
    return raw if raw["complete"] else r
