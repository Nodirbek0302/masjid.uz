"""Uzbek script helpers: Latin <-> Cyrillic transliteration and search-key normalisation.

Used (a) by search, so "masjid", "масжид", "мечеть" and "o'zbek" / "oʻzbek" / "ўзбек" find the same mosque
whichever script or apostrophe the name was typed in, and (b) by tools/gen_i18n.py to derive the Uzbek-Cyrillic
interface strings from the Latin ones.
"""
import re
import unicodedata

_APOS = "ʻʼ‘’`´ʹ′"
_APOS_RE = re.compile("[" + re.escape(_APOS) + "]")

_C2L = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "қ": "q", "ғ": "g'", "ҳ": "h", "ў": "o'",
}
_L2C = {
    "a": "а", "b": "б", "d": "д", "f": "ф", "g": "г", "h": "ҳ", "i": "и", "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "x": "х", "y": "й", "z": "з", "c": "ц", "e": "е",
    "w": "в",
}
_VOWELS = "aeiouаеёиоуэюяўыъ"


def norm_apos(s):
    """All apostrophe-like characters -> ASCII '."""
    return _APOS_RE.sub("'", s)


def strip_apos(s):
    return s.replace("'", "")


def _case(src, out):
    if len(src) > 1 and src.isupper():
        return out.upper()
    if src[:1].isupper():
        return out[:1].upper() + out[1:]
    return out


def to_latin(s):
    """Cyrillic (Uzbek/Russian) -> Uzbek Latin, case preserved. Non-Cyrillic text is returned unchanged."""
    out = []
    for ch in s:
        m = _C2L.get(ch.lower())
        out.append(_case(ch, m) if m is not None and m != "" else ("" if m == "" else ch))
    return "".join(out)


def to_cyrillic(s):
    """Uzbek Latin -> Cyrillic (1995 alphabet), case preserved. Apostrophes of any kind are accepted."""
    s = norm_apos(unicodedata.normalize("NFC", s))
    low = s.lower()
    out, i, n = [], 0, len(s)
    while i < n:
        two = low[i:i + 2]
        nxt = low[i + 2:i + 3]
        ch = low[i]
        piece, step = None, 1
        if two == "o'":
            piece, step = "ў", 2
        elif two == "g'":
            piece, step = "ғ", 2
        elif two == "sh":
            piece, step = "ш", 2
        elif two == "ch":
            piece, step = "ч", 2
        elif two == "ts":
            piece, step = "ц", 2
        elif two == "yo" and nxt != "'":
            piece, step = "ё", 2
        elif two == "yu":
            piece, step = "ю", 2
        elif two == "ya":
            piece, step = "я", 2
        elif two == "ye":
            piece, step = "е", 2
        elif ch == "'":
            piece = "ъ" if i > 0 and low[i - 1].isalpha() else "'"
        elif ch == "e":
            prev = low[i - 1] if i else " "
            piece = "э" if not prev.isalpha() else "е"
        else:
            piece = _L2C.get(ch)
        if piece is None:
            out.append(s[i])
        else:
            out.append(_case(s[i:i + step], piece))
        i += step
    return "".join(out)


def fold(text):
    """Lowercase, NFC, ASCII apostrophes."""
    return norm_apos(unicodedata.normalize("NFC", text or "")).lower()


def search_variants(text):
    """The set of forms a name/query can be matched in: as typed, Latin, Cyrillic - each also without apostrophes."""
    base = fold(text)
    lat = to_latin(base)
    cyr = to_cyrillic(lat)
    forms = {base, strip_apos(base), lat, strip_apos(lat), cyr, strip_apos(cyr)}
    return sorted(f for f in forms if f.strip())


def search_key(name):
    """Stored in mosques.name_search so any form of a query can be matched with a plain LIKE."""
    return " ".join(search_variants(name)) if name else None


_PROTECT = re.compile(
    r"(\{[^}]*\}|AM/PM|/\w+|tiles\.openfreemap\.org|Yandex Maps|OpenStreetMap contributors|OpenStreetMap|Mini App|Telegram|HTTPS|VPN|"
    r"HH:MM|ODbL|ADMIN_TOKEN|ID|©|[\U0001F300-\U0001FAFF\u2600-\u27BF\u2190-\u21FF])")


def cyrillize(text):
    """Uzbek Latin text -> Cyrillic; brand names, /commands, {placeholders}, emoji and digits stay as they are."""
    return "".join(part if _PROTECT.fullmatch(part) else to_cyrillic(part) for part in _PROTECT.split(text))
