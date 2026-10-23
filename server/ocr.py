"""Optional OCR for photographed timetables: Tesseract (free, runs on our own server; the photo is never sent to a third party
and is not stored). Without the binary the bot falls back to typed times. Pillow, when installed, cleans the photo first."""
import io
import shutil
import subprocess

WANTED_LANGS = ("rus", "eng", "uzb", "uzb_cyrl")


def available():
    return shutil.which("tesseract") is not None


def _langs():
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=15).stdout.split()
    except Exception:
        return "eng"
    have = [lang for lang in WANTED_LANGS if lang in out]
    return "+".join(have) or "eng"


def _prepare(data, mode="gray"):
    """Photo clean-up. mode "gray": grayscale, upscale, contrast, sharpen. mode "bw": additionally denoise and binarise (helps dark,
    noisy or uneven photos). Returns PNG bytes (the original bytes if Pillow is missing)."""
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        return data
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("L")
    if im.width < 1600:
        k = 1600.0 / im.width
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    if mode == "bw":
        im = ImageOps.autocontrast(im.filter(ImageFilter.MedianFilter(5)), cutoff=2)
        hist = im.histogram()                                   # Otsu threshold
        total, sum_all = sum(hist), sum(i * h for i, h in enumerate(hist))
        wb = sb = 0
        best, th = -1.0, 128
        for i, h in enumerate(hist):
            wb += h
            if wb == 0 or wb == total:
                continue
            sb += i * h
            mb, mf = sb / wb, (sum_all - sb) / (total - wb)
            var = wb * (total - wb) * (mb - mf) ** 2
            if var > best:
                best, th = var, i
        im = im.point(lambda v: 255 if v > th else 0)
    else:
        im = ImageOps.autocontrast(im, cutoff=1).filter(ImageFilter.SHARPEN)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _run(png, langs, psm, extra=()):
    cmd = ["tesseract", "stdin", "stdout", "-l", langs, "--psm", str(psm)] + list(extra)
    return subprocess.run(cmd, input=png, capture_output=True, timeout=45).stdout.decode("utf-8", "replace")


def read_texts(data):
    """Several OCR passes over the same photo (two clean-ups x layout modes, plus a digits-only pass); the caller keeps the best parse."""
    langs = _langs()
    outs = []
    for mode in ("gray", "bw"):
        png = _prepare(data, mode)
        for psm, extra in ((6, ()), (4, ()), (11, ()), (6, ("-c", "tessedit_char_whitelist=0123456789:.-"))):
            try:
                outs.append(_run(png, langs if not extra else "eng", psm, extra))
            except Exception:
                continue
    return outs
