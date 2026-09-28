#!/usr/bin/env python3
"""Discover every RSS link on G1's RSS directory and merge its items into JSONL.

Uses only the Python standard library. Run `python scrape_g1_rss.py --help` for options.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

PAGE_URL = "https://g1.globo.com/tecnologia/noticia/2012/11/siga-o-g1-por-rss.html"
DEFAULT_OUTPUT = "g1_articles.jsonl"
USER_AGENT = "G1RSSMerger/1.0 (public RSS reader; Python urllib)"


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("a", "link"):
            for name, value in attrs:
                if name.lower() == "href" and value:
                    self.hrefs.append(value)


def discover_feeds(page_html, page_url=PAGE_URL):
    """Return unique RSS2 links listed on the directory page, in page order."""
    parser = Links()
    parser.feed(page_html)
    urls = []
    seen = set()
    for href in parser.hrefs:
        # The old page contains at least one href ending in an encoded space.
        url = unquote(urljoin(page_url, href.strip())).strip()
        parts = urlsplit(url)
        if (parts.hostname != "g1.globo.com" or
                not parts.path.startswith("/dynamo/") or
                not parts.path.endswith("/rss2.xml") or
                parts.scheme not in ("http", "https")):
            continue
        clean = urlunsplit(("https", "g1.globo.com", parts.path, "", ""))
        if clean not in seen:
            seen.add(clean)
            urls.append(clean)
    return urls


def download(url, timeout=20, attempts=3):
    """Fetch bytes with bounded retries for temporary network/server failures."""
    for attempt in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT,
                                        "Accept": "application/rss+xml, application/xml, text/xml, text/html"})
            with urlopen(req, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except URLError:
            if attempt == attempts - 1:
                raise
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def published_at(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def parse_feed(data, source_url):
    """Parse an RSS 2.0 channel into article dictionaries."""
    root = ET.fromstring(data)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("not an RSS channel")
    articles = []
    for item in channel.findall("item"):
        def text(name):
            element = item.find(name)
            return "" if element is None else "".join(element.itertext()).strip()

        link = text("link")
        guid = text("guid")
        title = text("title")
        date = text("pubDate")
        if not (link or guid or title):
            continue
        articles.append({
            "title": title,
            "link": link,
            "guid": guid,
            "description": text("description"),
            "published": published_at(date),
            "pub_date_raw": date,
            "categories": list(dict.fromkeys(
                (el.text or "").strip() for el in item.findall("category") if (el.text or "").strip()
            )),
            "source_feeds": [source_url],
        })
    return articles


def article_key(article):
    """Prefer canonical story URL; fall back to GUID, then title and date."""
    for value in (article["link"], article["guid"]):
        parts = urlsplit(value.strip())
        if parts.scheme in ("http", "https") and parts.hostname:
            # G1 publishes the same story in several sections, occasionally
            # with different tracking parameters or a trailing slash.
            return ("url", parts.hostname.lower(), parts.path.rstrip("/") or "/")
    if article["guid"]:
        return ("guid", article["guid"])
    return ("title", article["title"].casefold(), article["pub_date_raw"])


def merge(feed_results):
    """Keep first occurrence and record every feed in which it appeared."""
    by_key = {}
    for articles in feed_results:
        for article in articles:
            key = article_key(article)
            if key in by_key:
                saved = by_key[key]
                for feed in article["source_feeds"]:
                    if feed not in saved["source_feeds"]:
                        saved["source_feeds"].append(feed)
            else:
                by_key[key] = article
    return sorted(by_key.values(), key=lambda a: (a["published"] or "", a["link"], a["title"]), reverse=True)


def write_jsonl(articles, output):
    """Replace output atomically to avoid leaving a truncated file on errors."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".g1-", suffix=".tmp", delete=False) as stream:
            tmp_name = stream.name
            for article in articles:
                stream.write(json.dumps(article, ensure_ascii=False) + "\n")
        Path(tmp_name).replace(output)
    finally:
        if tmp_name and Path(tmp_name).exists():
            Path(tmp_name).unlink()


def run(page_url, output, workers=4, timeout=20, page_file=None):
    if page_file:
        html = Path(page_file).read_text(encoding="utf-8")
    else:
        html = download(page_url, timeout=timeout).decode("utf-8", errors="replace")
    feeds = discover_feeds(html, page_url)
    if not feeds:
        raise ValueError("no G1 RSS feed links found on the directory page")
    print(f"Found {len(feeds)} unique RSS feeds", file=sys.stderr)
    results = {}
    errors = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        jobs = {executor.submit(lambda url: parse_feed(download(url, timeout=timeout), url), url): url
                for url in feeds}
        for future in as_completed(jobs):
            url = jobs[future]
            try:
                results[url] = future.result()
                print(f"OK {len(results[url]):3} items: {url}", file=sys.stderr)
            except (HTTPError, URLError, ET.ParseError, ValueError, TimeoutError, OSError) as exc:
                errors[url] = str(exc)
                print(f"FAILED {url}: {exc}", file=sys.stderr)
    if not results:
        raise RuntimeError("all feeds failed; refusing to replace the output")
    articles = merge(results[url] for url in feeds if url in results)
    write_jsonl(articles, output)
    print(f"Wrote {len(articles)} unique articles from {len(results)}/{len(feeds)} feeds to {output}",
          file=sys.stderr)
    if errors:
        print("WARNING: output is partial; retry the failed feeds before treating it as complete.",
              file=sys.stderr)
    return 2 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-url", default=PAGE_URL, help="RSS directory URL")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="destination JSONL file")
    parser.add_argument("--workers", type=int, default=4, help="simultaneous feed requests (default: 4)")
    parser.add_argument("--timeout", type=float, default=20, help="per-request timeout in seconds")
    parser.add_argument("--page-file", type=Path, help="local HTML directory page (for testing/offline use)")
    args = parser.parse_args()
    if args.workers < 1 or args.timeout <= 0:
        parser.error("--workers and --timeout must be positive")
    try:
        return run(args.page_url, args.output, args.workers, args.timeout, args.page_file)
    except (HTTPError, URLError, ET.ParseError, ValueError, TimeoutError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
