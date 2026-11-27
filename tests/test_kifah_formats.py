"""Kifah format / noise / safety tests (§3.7)."""

from __future__ import annotations

import unittest
from datetime import datetime

from scraper.common import BAGHDAD, parse_text_core
from scraper.kifah import parse_kifah_page, parse_text
from scraper.model import SourceShapeError


def hit(text: str, market: str):
    hits, ps = parse_text_core(text)
    for h in hits:
        if h.market == market:
            return h, ps
    raise AssertionError(f"no {market} in {text!r} status={ps} hits={hits}")


class TestKifahFormats(unittest.TestCase):
    def test_format_a_short(self):
        h, _ = hit("🟢 كفاح 168,800", "kifah")
        self.assertEqual(h.status, "ok")
        self.assertEqual(h.mid, 168800)

    def test_format_b_bid_ask(self):
        h, _ = hit("🟢 كفاح مطلوب 170,000 معروض 170,050", "kifah")
        self.assertEqual(h.status, "ok")
        self.assertEqual(h.bid, 170000)
        self.assertEqual(h.ask, 170050)
        self.assertEqual(h.mid, 170025)

    def test_emoji_between_tokens(self):
        h, _ = hit("🟢 كفاح 🏠 مطلوب 167,900 💰 معروض 167,950 💰", "kifah")
        self.assertEqual(h.status, "ok")
        self.assertEqual(h.bid, 167900)
        self.assertEqual(h.ask, 167950)
        self.assertEqual(h.mid, 167925)

    def test_bid_only_combined(self):
        h, _ = hit("كفاح / حارثية / سموأل 169,700 مطلوب", "samawal")
        self.assertEqual(h.status, "ok")
        self.assertEqual(h.bid, 169700)
        self.assertEqual(h.mid, 169700)
        self.assertIn("bid_only", h.warnings)
        self.assertIn("combined_post", h.warnings)

    def test_ask_only_dot_thousands_combined(self):
        h, _ = hit("الكفاح والحارثية 169.800 عرض", "kifah")
        self.assertEqual(h.status, "ok")
        self.assertEqual(h.ask, 169800)
        self.assertEqual(h.mid, 169800)
        self.assertIn("ask_only", h.warnings)
        self.assertIn("combined_post", h.warnings)

    def test_combined_slash(self):
        hits, ps = parse_text_core("🟢 سموأل / كفاح / حارثية 169,300")
        self.assertEqual(ps, "rate_post")
        mids = {h.market: h.mid for h in hits}
        self.assertEqual(mids["kifah"], 169300)
        self.assertEqual(mids["harithiya"], 169300)
        self.assertEqual(mids["samawal"], 169300)

    def test_aliases_alkifah_alharithiya(self):
        h, _ = hit("🟢 الكفاح 168,000", "kifah")
        self.assertEqual(h.mid, 168000)
        h2, _ = hit("الحارثية 169.000 عرض", "harithiya")
        self.assertEqual(h2.ask, 169000)

    def test_no_separator_six_digits(self):
        h, _ = hit("🟢 كفاح / حارثية / سموأل 169400", "kifah")
        self.assertEqual(h.mid, 169400)

    def test_missing_space(self):
        h, _ = hit("🟢 حارثية169,500", "harithiya")
        self.assertEqual(h.mid, 169500)

    def test_trailing_emoji(self):
        h, _ = hit("🟢 دهوك 169,500 🟢", "duhok")
        self.assertEqual(h.mid, 169500)

    def test_two_bare_numbers(self):
        h, _ = hit("🟢 الموصل 169,850 170,200", "mosul")
        self.assertEqual(h.status, "ok")
        self.assertEqual(h.bid, 169850)
        self.assertEqual(h.ask, 170200)
        self.assertEqual(h.mid, 170025)
        self.assertIn("two_numbers_assumed_bid_ask", h.warnings)

    def test_arabic_indic_digits_synthetic(self):
        # synthetic variant of a real sample
        h, _ = hit("كفاح ١٦٨,٨٠٠", "kifah")
        self.assertEqual(h.status, "ok")
        self.assertEqual(h.mid, 168800)

    def test_arabic_thousands_sep_synthetic(self):
        h, _ = hit("كفاح ١٦٨٬٨٠٠", "kifah")
        self.assertEqual(h.mid, 168800)


class TestNoise(unittest.TestCase):
    def test_promo_whatsapp(self):
        hits, ps = parse_text(
            "‏متابعة قناة بورصة الكفاح على واتساب: https://whatsapp.com/channel/0029 بغداد 170.650 🔥"
        )
        self.assertEqual(ps, "promo_skipped")
        self.assertEqual(hits, [])

    def test_promo_tme(self):
        hits, ps = parse_text("https://t.me/+abc الي يحب يدخل")
        self.assertEqual(ps, "promo_skipped")
        self.assertEqual(hits, [])

    def test_fx_euro(self):
        hits, ps = parse_text("اليورو 187,400")
        self.assertEqual(ps, "fx_skipped")
        self.assertEqual(hits, [])

    def test_fx_toman(self):
        hits, ps = parse_text("التومان الايراني 100$ = 27,360,000")
        self.assertEqual(ps, "fx_skipped")

    def test_qualifier(self):
        hits, ps = parse_text("170,950 انباع ع باجر 🔻")
        self.assertEqual(ps, "qualifier_skipped")
        self.assertEqual(hits, [])

    def test_pinned_deleted(self):
        hits, ps = parse_text("بورصة الكفاح pinned Deleted message")
        self.assertEqual(ps, "not_a_rate")


class TestSafety(unittest.TestCase):
    def test_number_before_name_is_baghdad_not_kifah(self):
        hits, ps = parse_text_core("171,200 بغداد 🔻")
        self.assertEqual(ps, "rate_post")
        markets = {h.market for h in hits}
        self.assertIn("baghdad", markets)
        self.assertNotIn("kifah", markets)
        h = next(h for h in hits if h.market == "baghdad")
        self.assertEqual(h.mid, 171200)
        self.assertIn("number_before_name", h.warnings)

    def test_out_of_range_short(self):
        h, _ = hit("🟢 كفاح 16,800", "kifah")
        self.assertEqual(h.status, "out_of_range")

    def test_out_of_range_too_big(self):
        h, _ = hit("🟢 كفاح 1,688,000", "kifah")
        self.assertEqual(h.status, "out_of_range")

    def test_crossed(self):
        h, _ = hit("🟢 كفاح مطلوب 170,500 معروض 170,000", "kifah")
        self.assertEqual(h.status, "crossed")

    def test_wide_spread(self):
        h, _ = hit("🟢 كفاح مطلوب 160,000 معروض 170,000", "kifah")
        self.assertEqual(h.status, "wide_spread")

    def test_zero_posts_raises(self):
        with self.assertRaises(SourceShapeError):
            parse_kifah_page("<html><body>no wraps</body></html>", datetime.now(BAGHDAD))


if __name__ == "__main__":
    unittest.main()
