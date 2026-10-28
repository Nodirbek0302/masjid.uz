"""Typed-time parser (timetable.py): the parts that survived the move from Tesseract/Whisper to Gemini."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402,F401


class TimetableParser(unittest.TestCase):
    def test_parser_cases(self):
        import timetable as T
        five = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}
        cases = {
            "Bomdod 05:20\nQuyosh 06:40\nPeshin 12:30\nAsr 16:00\nShom 17:35\nXufton 19:00": five,
            "Бомдод 5.20\nПешин 12-30\nАср 16:00\nШом 17:35\nХуфтон 19:00": five,
            "Фаджр 05:20\nВосход 06:40\nЗухр 12:30\nАср 16:00\nМагриб 17:35\nИша 19:00": five,
            "05:20 12:30 16:00 17:35 19:00": five,
            "05:20 06:40 12:30 16:00 17:35 19:00": five,
            "Bomdod O5:2O\nPeshin l2:30\nAsr l6:OO\nShom l7:35\nXufton l9:OO": five,       # OCR confuses O/0 and l/1
        }
        for text, want in cases.items():
            r = T.parse(text)
            self.assertTrue(r["complete"], text); self.assertEqual(r["times"], want, text)
        five_ = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}
        wide_ = {                                                                       # long boards: names in one line, times below
            "Bomdod Quyosh Peshin Asr Shom Xufton\n05:20 06:40 12:30 16:00 17:35 19:00": five_,
            "Бомдод Пешин Аср Шом Хуфтон\n05:20 12:30 16:00 17:35 19:00": five_,
            "NAMOZ VAQTLARI\nBomdod  Peshin  Asr  Shom  Xufton\n05:20   12:30   16:00   17:35   19:00": five_,
        }
        for text, want in wide_.items():
            r = T.parse(text); self.assertTrue(r["complete"], text); self.assertEqual(r["times"], want, text)
        rows = T.parse("Bomdod Peshin Asr Shom Xufton\nAzon 05:40 12:45 16:15 17:40 19:15\nJamoat 05:20 12:30 16:00 17:35 19:00")
        self.assertFalse(rows["complete"]); self.assertIn("two_columns", rows["warnings"])          # two rows -> the user picks
        self.assertEqual({rows["times"]["fajr"], rows["alt"]["fajr"]}, {"05:40", "05:20"})
        two = T.parse("Bomdod 05:20 05:40\nPeshin 12:30 12:45\nAsr 16:00 16:15\nShom 17:35 17:40\nXufton 19:00 19:15")
        self.assertFalse(two["complete"]); self.assertEqual(two["alt"]["fajr"], "05:40"); self.assertIn("two_columns", two["warnings"])
        dated = T.parse("JOME MASJIDI\n23.10.2026\n05:20\n06:40\n12:30\n16:00\n17:35\n19:00")           # a date line must not become 23:10
        self.assertTrue(dated["complete"]); self.assertEqual(dated["times"]["fajr"], "05:20"); self.assertIn("sunrise_dropped", dated["warnings"])
        self.assertTrue(T.parse("23/10/2026 05:20 12:30 16:00 17:35 19:00")["complete"])
        rep = T.parse("Bomdod 05:20\nPeshin 12:30\nAsr 6:00\nShom 17:35\nXufton 19:00")           # OCR lost the "1" of 16:00
        self.assertTrue(rep["complete"]); self.assertEqual(rep["times"]["asr"], "16:00"); self.assertIn("ampm_resolved", rep["warnings"])
        self.assertFalse(T.parse("05:20 12:30 11:00 03:00 19:00")["complete"])                  # not repairable -> never complete
        weird = T.parse("Bomdod 09:20\nPeshin 12:30\nAsr 16:00\nShom 17:35\nXufton 19:00")      # fajr at 09:20 cannot be right
        self.assertFalse(weird["complete"]); self.assertIn("implausible", weird["warnings"])
        q = T.parse("AZON - JAMOAT\nBomdod Q5:40 05:20\nPeshin 12:45 12:30\nAsr 16:15 16:00\nShom 17:40 17:35\nXufton 19:15 19:00")      # OCR read the 0 of 05:40 as Q
        self.assertEqual(q["alt"]["fajr"], "05:20"); self.assertIn("two_columns", q["warnings"]); self.assertFalse(q["complete"])
        mixed = T.parse("Bomdod 05:40 05:20\nPeshin 12:45 12:30\nAsr 16:15 16:00\nShom 17:40\nXufton 19:15")   # a lost time must not mix the columns silently
        self.assertFalse(mixed["complete"]); self.assertIn("inconsistent_columns", mixed["warnings"])
        for bad in ("Bomdod 05:20\nAsr 16:00", "Assalomu alaykum 2026", "05:20 12:30 11:00 17:35 19:00", "99:99 88:88", ""):
            self.assertFalse(T.parse(bad)["complete"], bad)                # never "complete" on doubtful input



class AmPmAndNotations(unittest.TestCase):
    FIVE = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}

    def test_twelve_hour_times_without_am_pm_land_in_the_right_half_of_the_day(self):
        import timetable as T
        r = T.parse("5:20 12:30 4:00 5:35 7:00")
        self.assertTrue(r["complete"]); self.assertEqual(r["times"], self.FIVE)
        r = T.parse("Bomdod 5:20\nPeshin 1:15\nAsr 4:05\nShom 5:40\nXufton 7:10")
        self.assertEqual(r["times"], {"fajr": "05:20", "dhuhr": "13:15", "asr": "16:05", "maghrib": "17:40", "isha": "19:10"})
        r = T.parse("Fajr 4:10\nDhuhr 12:00\nAsr 5:30\nMaghrib 8:20\nIsha 10:15")              # summer: late sunset, isha 22:15
        self.assertEqual(r["times"], {"fajr": "04:10", "dhuhr": "12:00", "asr": "17:30", "maghrib": "20:20", "isha": "22:15"})
        r = T.parse("Fajr 5:20 AM\nDhuhr 12:30 PM\nAsr 4:00 PM\nMaghrib 5:35 PM\nIsha 7:00 PM")  # explicit markers
        self.assertEqual(r["times"], self.FIVE)
        r = T.parse("5:20 6:00 7:00 8:00 9:00")                                                # nonsense stays unsaved
        self.assertFalse(r["complete"])

    def test_asr_six_oclock_is_16_or_18_depending_on_the_rest_of_the_day(self):
        import timetable as T
        winter = {"fajr": "05:20", "dhuhr": "12:30", "asr": "16:00", "maghrib": "17:35", "isha": "19:00"}
        summer = {"fajr": "04:10", "dhuhr": "12:30", "asr": "18:00", "maghrib": "19:45", "isha": "21:30"}
        typed = {
            "Bomdod 5:20\nPeshin 12:30\nAsr 6:00\nShom 5:35\nXufton 7:00": winter,                 # photo text / typed, no markers
            "Bomdod 4:10\nPeshin 12:30\nAsr 6:00\nShom 7:45\nXufton 9:30": summer,
            "Bomdod 4:10\nPeshin 12:30\nAsr 6:00 PM\nShom 7:45 PM\nXufton 9:30 PM": summer,
            "Bomdod 5:20\nPeshin 12:30\nAsr 16:00\nShom 17:35\nXufton 19:00": winter,              # 24-hour is left alone
            "Bomdod 4:10\nPeshin 12:30\nAsr 18:00\nShom 19:45\nXufton 21:30": summer,
            "Bomdod 5:20\nPeshin 12:30\nAsr l6:00\nShom l7:35\nXufton l9:00": winter,              # OCR "l" for "1"
            "4:10 12:30 6:00 7:45 9:30": summer,                                                  # no names at all
            "5:20 12:30 6:00 5:35 7:00": winter,
        }
        for text, want in typed.items():
            r = T.parse(text); self.assertTrue(r["complete"], text); self.assertEqual(r["times"], want, text)

    def test_every_region_and_month_in_every_notation(self):
        """Real prayer times (14 regions x 12 months x 3 days) written the ways boards and speakers write them must come back exactly."""
        import prayertimes as P
        import timetable as T
        names = {"fajr": "Bomdod", "dhuhr": "Peshin", "asr": "Asr", "maghrib": "Shom", "isha": "Xufton"}
        def h12(t):
            return "%d:%s" % ((int(t[:2]) % 12) or 12, t[3:])
        notations = {
            "24h": lambda t: t,
            "24h without zero": lambda t: t.lstrip("0") if t[0] == "0" else t,
            "12h without marker": h12,
            "12h with marker": lambda t: h12(t) + (" AM" if int(t[:2]) < 12 else " PM"),
            "lost leading 1": lambda t: t[1:] if t[0] == "1" else t,
        }
        for lat, lon in P.REGIONS:
            for month in range(1, 13):
                for day in (3, 12, 24):
                    tt = P.times(2026, month, day, lat, lon)
                    truth = {k: P.hm(tt[k]) for k in names}
                    for nn, f in notations.items():
                        r = T.parse("\n".join("%s %s" % (names[k], f(truth[k])) for k in names))
                        self.assertTrue(r["complete"], (nn, lat, month, day, truth, r))
                        self.assertEqual(r["times"], truth, (nn, lat, month, day))


if __name__ == "__main__":
    unittest.main()
