import unittest

from scripts.update_news import (
    group_stats,
    maybe_fix_mojibake,
    normalize_source_for_display,
    parse_feed_entries_via_xml,
)


class TopicFilterTests(unittest.TestCase):
    def test_source_fallback_to_host(self):
        source = normalize_source_for_display("opmlrss", "", "https://news.ycombinator.com/item?id=1")
        self.assertEqual(source, "news.ycombinator.com")

    def test_fix_mojibake(self):
        raw = "è°å¨ç¼åä»£ç "
        self.assertEqual(maybe_fix_mojibake(raw), "谁在编写代码")

    def test_parse_feed_entries_via_xml(self):
        xml = b"""<?xml version='1.0' encoding='UTF-8'?>
<rss><channel>
<item><title>A</title><link>https://x/a</link><pubDate>2026-02-20</pubDate></item>
</channel></rss>"""
        items = parse_feed_entries_via_xml(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "A")

    def test_group_stats(self):
        items = [
            {
                "site_id": "opmlrss",
                "site_name": "OPML RSS",
                "source": "FeedA",
            },
            {
                "site_id": "opmlrss",
                "site_name": "OPML RSS",
                "source": "FeedB",
            },
        ]
        site_stats, source_count = group_stats(items)
        self.assertEqual(len(site_stats), 1)
        self.assertEqual(site_stats[0]["site_id"], "opmlrss")
        self.assertEqual(site_stats[0]["count"], 2)
        self.assertEqual(source_count, 2)


if __name__ == "__main__":
    unittest.main()
