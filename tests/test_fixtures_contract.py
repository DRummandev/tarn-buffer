"""Fixture contract + 300-post property tests (§3.7)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scraper.common import parse_text_core

FIX = Path(__file__).parent / "fixtures"


class TestFixtureContract(unittest.TestCase):
    def test_real_samples_kifah_expect(self):
        data = json.loads((FIX / "real_samples.json").read_text(encoding="utf-8"))
        for sample in data["kifah_posts"]:
            text = sample["Text"]
            expect = sample["expect"]
            hits, ps = parse_text_core(text)
            if expect is None:
                self.assertEqual(
                    hits,
                    [],
                    f"{sample['ID']}: expected skip, got {hits} ps={ps}",
                )
                self.assertEqual(
                    ps,
                    sample["PostStatus"],
                    f"{sample['ID']}: post status {ps} != {sample['PostStatus']}",
                )
                continue
            self.assertEqual(ps, "rate_post", sample["ID"])
            got = {(h.market, h.status): h for h in hits}
            for exp in expect:
                key = (exp["market"], exp["status"])
                self.assertIn(key, got, f"{sample['ID']}: missing {key} in {list(got)}")
                h = got[key]
                if "bid" in exp:
                    self.assertEqual(h.bid, exp["bid"], sample["ID"])
                if "ask" in exp:
                    self.assertEqual(h.ask, exp["ask"], sample["ID"])
                if "mid" in exp:
                    self.assertEqual(h.mid, exp["mid"], sample["ID"])
                if "warnings" in exp:
                    for w in exp["warnings"]:
                        self.assertIn(w, h.warnings or [], f"{sample['ID']} warn {w}")


class TestProperty300(unittest.TestCase):
    def test_all_300_no_wrong_number(self):
        posts = json.loads(
            (FIX / "kifah_posts_300_real.json").read_text(encoding="utf-8")
        )
        n_ok = 0
        for pid, pair in posts.items():
            text = pair[1]
            hits, _ = parse_text_core(text)
            for h in hits:
                if h.status == "ok":
                    n_ok += 1
                    self.assertGreaterEqual(h.mid, 120000, pid)
                    self.assertLessEqual(h.mid, 220000, pid)
        self.assertGreaterEqual(n_ok, 150, f"only {n_ok} ok hits from 300 posts")


if __name__ == "__main__":
    unittest.main()
