"""Photos of mosques from OpenStreetMap tags. Only Wikimedia Commons is trusted: `wikimedia_commons=File:...` or an `image=` link to
Commons / upload.wikimedia.org. The thumbnail address (on upload.wikimedia.org, worked out from the file name: no request to Wikimedia
is made) and the file's Commons page (the credit link) are stored; the picture itself is never copied, browsers load it from Wikimedia."""
import hashlib
import re
import urllib.parse

THUMB_PX = 500                                    # one of Wikimedia's standard thumbnail widths: light enough for a list on a weak phone
_COMMONS_FILE = re.compile(r"https?://commons\.wikimedia\.org/wiki/(File:[^?#]+)")
_UPLOAD = re.compile(r"https://upload\.wikimedia\.org/\S+")
_RASTER = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def commons_title(tags):
    """('title', 'File:x.jpg') | ('url', direct upload.wikimedia.org address) | None. Categories are not followed (that needs the API)."""
    wc = (tags.get("wikimedia_commons") or "").strip()
    if wc.startswith("File:"):
        return ("title", wc)
    img = (tags.get("image") or "").strip()
    m = _COMMONS_FILE.match(img)
    if m:
        return ("title", urllib.parse.unquote(m.group(1)))
    if _UPLOAD.fullmatch(img):
        return ("url", img.split("?")[0])
    return None


def thumb_for(title):
    """(thumbnail url, Commons page) for 'File:Name.jpg', or None for a file type that cannot be shown as a picture."""
    name = title[len("File:"):].strip().replace(" ", "_")
    if not name or "/" in name or not name.lower().endswith(_RASTER):
        return None
    h = hashlib.md5(name.encode()).hexdigest()
    q = urllib.parse.quote(name, safe="()!,'~*-._")
    return ("https://upload.wikimedia.org/wikipedia/commons/thumb/%s/%s/%s/%dpx-%s" % (h[0], h[:2], q, THUMB_PX, q),
            "https://commons.wikimedia.org/wiki/File:" + q)


def photo_for(tags):
    """(photo_url, photo_credit) for an OSM object, or None."""
    ref = commons_title(tags)
    if not ref:
        return None
    return (ref[1], None) if ref[0] == "url" else thumb_for(ref[1])
