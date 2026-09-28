import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import scrape_g1_rss as scraper

PAGE = '''<html><a href="https://g1.globo.com/dynamo/rss2.xml">G1</a>
<a href="/dynamo/tecnologia/rss2.xml">Tecnologia</a>
<a href="https://g1.globo.com/dynamo/sao-paulo/rss2.xml%20">São Paulo</a>
<a href="https://g1.globo.com/dynamo/rss2.xml?utm_source=x">Duplicate</a>
<a href="https://example.com/dynamo/rss2.xml">Not G1</a></html>'''
ALL = '''<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>G1</title>
<item><title>Noticia A</title><link>https://g1.globo.com/a.ghtml?utm_source=rss</link>
<guid>https://g1.globo.com/a.ghtml</guid><description><![CDATA[Primeira <b>notícia</b>]]></description>
<pubDate>Mon, 28 Sep 2026 10:30:00 -0300</pubDate><category>Brasil</category></item>
<item><title>Noticia B</title><link>https://g1.globo.com/b.ghtml</link>
<pubDate>Mon, 28 Sep 2026 10:00:00 -0300</pubDate></item>
</channel></rss>'''.encode("utf-8")
TECH = b'''<rss version="2.0"><channel>
<item><title>Noticia A</title><link>https://g1.globo.com/a.ghtml#share</link>
<guid>other-id</guid><pubDate>Mon, 28 Sep 2026 13:30:00 GMT</pubDate></item>
<item><title>Noticia C</title><guid>unique-id</guid><title>Noticia C</title></item>
</channel></rss>'''
SP = '''<rss version="2.0"><channel><item><title>São Paulo</title>
<link>https://g1.globo.com/sp.ghtml</link></item></channel></rss>'''.encode("utf-8")


class ScraperTests(unittest.TestCase):
    def test_discovery_normalizes_and_deduplicates(self):
        self.assertEqual(scraper.discover_feeds(PAGE), [
            "https://g1.globo.com/dynamo/rss2.xml",
            "https://g1.globo.com/dynamo/tecnologia/rss2.xml",
            "https://g1.globo.com/dynamo/sao-paulo/rss2.xml",
        ])

    def test_parse_rejects_non_rss(self):
        with self.assertRaises(ValueError):
            scraper.parse_feed(b"<html><body>error</body></html>", "https://g1.globo.com/dynamo/rss2.xml")

    def test_end_to_end_and_repeatable_output(self):
        feeds = scraper.discover_feeds(PAGE)
        content = dict(zip(feeds, (ALL, TECH, SP)))
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "page.html"
            output = Path(directory) / "articles.jsonl"
            page.write_text(PAGE, encoding="utf-8")
            with patch.object(scraper, "download", side_effect=lambda url, timeout=20: content[url]):
                self.assertEqual(scraper.run(scraper.PAGE_URL, output, page_file=page), 0)
                first = output.read_bytes()
                self.assertEqual(scraper.run(scraper.PAGE_URL, output, page_file=page), 0)
                self.assertEqual(output.read_bytes(), first)
            rows = [json.loads(line) for line in first.decode().splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["published"], "2026-09-28T13:30:00+00:00")
            a = next(row for row in rows if row["title"] == "Noticia A")
            self.assertEqual(a["source_feeds"], feeds[:2])
            self.assertEqual(a["description"], "Primeira <b>notícia</b>")

    def test_partial_run_reports_failure_and_keeps_successes(self):
        feeds = scraper.discover_feeds(PAGE)
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "page.html"
            output = Path(directory) / "articles.jsonl"
            page.write_text(PAGE, encoding="utf-8")
            def download(url, timeout=20):
                if url == feeds[0]:
                    return ALL
                raise URLError("unavailable")
            with patch.object(scraper, "download", side_effect=download):
                self.assertEqual(scraper.run(scraper.PAGE_URL, output, page_file=page), 2)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 2)
            with patch.object(scraper, "download", side_effect=URLError("unavailable")):
                with self.assertRaises(RuntimeError):
                    scraper.run(scraper.PAGE_URL, output, page_file=page)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
