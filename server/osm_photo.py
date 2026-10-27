"""Photos of mosques from OpenStreetMap tags. Only Wikimedia Commons is trusted: `wikimedia_commons=File:...|Category:...`
or an `image=` link to Commons. The thumbnail address (upload.wikimedia.org / thumb.wikimedia.org) and the file's Commons page (the credit link) are
stored; the picture itself is never copied, clients load it from Wikimedia."""
import json
import os
import re
import urllib.parse
import urllib.request

API = "https://commons.wikimedia.org/w/api.php"
UA = "masjid-uz-import/0.1 (%s)" % os.environ.get("OSM_CONTACT", "no contact set")
THUMB_PX = 500                                    # one of Wikimedia's standard thumbnail widths: light enough for a list on a weak phone
_COMMONS_FILE = re.compile(r"https?://commons\.wikimedia\.org/wiki/(File:[^?#]+)")
_UPLOAD = re.compile(r"https://(?:upload|thumb)\.wikimedia\.org/\S+")


def commons_title(tags):
    """The Commons title ('File:x.jpg' or 'Category:y') the tags point to, a direct upload.wikimedia.org picture
    ('url', address), or None."""
    wc = (tags.get("wikimedia_commons") or "").strip()
    if wc.startswith(("File:", "Category:")):
        return ("title", wc)
    img = (tags.get("image") or "").strip()
    m = _COMMONS_FILE.match(img)
    if m:
        return ("title", urllib.parse.unquote(m.group(1)))
    if _UPLOAD.fullmatch(img):
        return ("url", img)
    return None


def _clean(url):
    return url.split("?")[0] if url else url          # drop the tracking parameters Commons adds


def _query(params):
    url = API + "?" + urllib.parse.urlencode(dict(params, action="query", format="json", prop="imageinfo",
                                                  iiprop="url|mime", iiurlwidth=THUMB_PX))
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
        return json.load(r)


def commons_lookup(title):
    """(thumbnail url, Commons page) of a file, or of the first picture in a category; None when there is none."""
    params = ({"generator": "categorymembers", "gcmtitle": title, "gcmtype": "file", "gcmlimit": 10} if title.startswith("Category:")
              else {"titles": title})
    pages = (_query(params).get("query") or {}).get("pages") or {}
    best = None
    for p in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        if not info.get("thumburl") or not str(info.get("mime", "")).startswith("image/"):
            continue
        if info.get("mime") == "image/jpeg":
            return _clean(info["thumburl"]), info.get("descriptionurl")
        best = best or (_clean(info["thumburl"]), info.get("descriptionurl"))
    return best


def photo_for(tags, lookup=commons_lookup):
    """(photo_url, photo_credit) for an OSM object, or None."""
    ref = commons_title(tags)
    if not ref:
        return None
    if ref[0] == "url":
        return ref[1], None
    return lookup(ref[1])
