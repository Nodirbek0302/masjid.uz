#!/usr/bin/env python3
"""Draws sample prayer-timetable pictures for testing the Telegram bot's photo reading (needs Pillow; macOS fonts).

    .venv/bin/python tools/make_test_timetables.py [outdir]     -> docs/test-timetables/*.jpg
Every board shows the times 05:20 12:30 16:00 17:35 19:00 (the two-column one shows azon / jamoat).
"""
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "docs" / "test-timetables"
random.seed(3)


def board(name, title, rows, size=(900, 700), bg=(236, 246, 238), ink=(10, 60, 40), rot=0, blur=0.0, noise=0, dark=0.0, quality=70):
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, size[0] - 20, size[1] - 20], outline=ink, width=6)
    d.text((size[0] // 2, 70), title, font=ImageFont.truetype(BOLD, 44), fill=ink, anchor="mm")
    y, rh = 140, (size[1] - 200) // len(rows)
    for r in rows:
        d.line([40, y - 10, size[0] - 40, y - 10], fill=ink, width=2)
        d.text((70, y + rh // 2 - 10), r[0], font=ImageFont.truetype(BOLD, 40), fill=ink, anchor="lm")
        for i, t in enumerate(r[1:]):
            d.text((size[0] - 120 - i * 230, y + rh // 2 - 10), t, font=ImageFont.truetype(BOLD, 46), fill=ink, anchor="rm")
        y += rh
    if rot:
        im = im.rotate(rot, expand=True, fillcolor=(120, 120, 120), resample=Image.BICUBIC)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    if dark:
        im = Image.eval(im, lambda v: int(v * (1 - dark)))
    if noise:
        px = im.load()
        for _ in range(im.width * im.height // noise):
            x, yy, v = random.randrange(im.width), random.randrange(im.height), random.randrange(60, 200)
            px[x, yy] = (v, v, v)
    OUT.mkdir(parents=True, exist_ok=True)
    im.save(OUT / (name + ".jpg"), "JPEG", quality=quality)


T = ("05:20", "06:40", "12:30", "16:00", "17:35", "19:00")
board("01-lotin", "JOME MASJIDI · NAMOZ VAQTLARI", [("Bomdod", T[0]), ("Quyosh", T[1]), ("Peshin", T[2]), ("Asr", T[3]), ("Shom", T[4]), ("Xufton", T[5])])
board("02-kirill-ozbek", "ЖОМЕ МАСЖИДИ · НАМОЗ ВАҚТЛАРИ", [("Бомдод", T[0]), ("Қуёш", T[1]), ("Пешин", T[2]), ("Аср", T[3]), ("Шом", T[4]), ("Хуфтон", T[5])])
board("03-rus", "МЕЧЕТЬ · РАСПИСАНИЕ НАМАЗОВ", [("Фаджр", T[0]), ("Восход", T[1]), ("Зухр", T[2]), ("Аср", T[3]), ("Магриб", T[4]), ("Иша", T[5])])
board("04-ikki-ustun-azon-jamoat", "NAMOZ VAQTLARI · AZON / JAMOAT",
      [("Bomdod", "05:40", "05:20"), ("Peshin", "12:45", "12:30"), ("Asr", "16:15", "16:00"), ("Shom", "17:40", "17:35"), ("Xufton", "19:15", "19:00")])
board("05-qiyshiq-xira", "NAMOZ VAQTLARI", [("Bomdod", T[0]), ("Peshin", T[2]), ("Asr", T[3]), ("Shom", T[4]), ("Xufton", T[5])], rot=4, blur=1.1, noise=40, dark=0.25)
board("06-qorongi", "NAMOZ VAQTLARI", [("Bomdod", T[0]), ("Peshin", T[2]), ("Asr", T[3]), ("Shom", T[4]), ("Xufton", T[5])], rot=-7, blur=2.0, noise=25, dark=0.45, bg=(200, 200, 190))
board("07-yangi-vaqtlar", "VAQTLAR O'ZGARDI", [("Bomdod", "05:25"), ("Peshin", "12:32"), ("Asr", "16:05"), ("Shom", "17:38"), ("Xufton", "19:05")])


def wide(name, title, labels, rows, bg=(12, 12, 14), head=(230, 230, 230), digit=(255, 150, 20), size=(1800, 460), rot=0, blur=0.0, noise=0, quality=72):
    """A long horizontal board: prayer names in a header row, one or more rows of times underneath (like LED boards in mosques)."""
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    d.rectangle([12, 12, size[0] - 12, size[1] - 12], outline=head, width=4)
    d.text((size[0] // 2, 52), title, font=ImageFont.truetype(BOLD, 40), fill=head, anchor="mm")
    n = len(labels)
    x0, x1 = 230 if len(rows[0]) > n else 60, size[0] - 60
    cw = (x1 - x0) / n
    for i, lab in enumerate(labels):
        d.text((x0 + cw * (i + 0.5), 130), lab, font=ImageFont.truetype(BOLD, 44), fill=head, anchor="mm")
    rh = (size[1] - 190) // len(rows)
    for r, row in enumerate(rows):
        y = 190 + rh * r + rh // 2
        tag = None
        vals = row
        if len(row) > n:
            tag, vals = row[0], row[1:]
        if tag:
            d.text((40, y), tag, font=ImageFont.truetype(BOLD, 40), fill=head, anchor="lm")
        for i, v in enumerate(vals):
            d.text((x0 + cw * (i + 0.5), y), v, font=ImageFont.truetype(BOLD, 96), fill=digit, anchor="mm")
    if rot:
        im = im.rotate(rot, expand=True, fillcolor=(60, 60, 60), resample=Image.BICUBIC)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        px = im.load()
        for _ in range(im.width * im.height // noise):
            x, yy, v = random.randrange(im.width), random.randrange(im.height), random.randrange(40, 160)
            px[x, yy] = (v, v, v)
    OUT.mkdir(parents=True, exist_ok=True)
    im.save(OUT / (name + ".jpg"), "JPEG", quality=quality)


wide("08-uzun-led-6-ustun", "NAMOZ VAQTLARI", ["Bomdod", "Quyosh", "Peshin", "Asr", "Shom", "Xufton"], [list(T)])
wide("09-uzun-oq-5-ustun", "НАМОЗ ВАҚТЛАРИ", ["Бомдод", "Пешин", "Аср", "Шом", "Хуфтон"], [[T[0], T[2], T[3], T[4], T[5]]],
     bg=(245, 245, 240), head=(20, 70, 50), digit=(0, 110, 70))
wide("10-uzun-azon-jamoat-ikki-qator", "AZON VA JAMOAT VAQTLARI", ["Bomdod", "Peshin", "Asr", "Shom", "Xufton"],
     [["Azon", "05:40", "12:45", "16:15", "17:40", "19:15"], ["Jamoat", "05:20", "12:30", "16:00", "17:35", "19:00"]],
     bg=(18, 40, 30), head=(220, 240, 225), digit=(250, 220, 90), size=(1800, 560))
wide("11-uzun-qiyshiq-xira", "NAMOZ VAQTLARI", ["Bomdod", "Peshin", "Asr", "Shom", "Xufton"], [[T[0], T[2], T[3], T[4], T[5]]], rot=-3, blur=1.2, noise=45)
print("saved to", OUT)
