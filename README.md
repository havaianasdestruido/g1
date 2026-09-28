# G1 RSS merger

`python3 scrape_g1_rss.py` reads the RSS directory at [Siga o G1 por RSS](https://g1.globo.com/tecnologia/noticia/2012/11/siga-o-g1-por-rss.html), discovers every linked G1 `/dynamo/.../rss2.xml` feed, downloads the feeds, deduplicates their articles, and writes `g1_articles.jsonl` in UTF-8. No third-party Python packages are needed.

```sh
python3 scrape_g1_rss.py --output g1_articles.jsonl
python3 -m unittest discover -s tests -v
```

Each JSONL line is one RSS item with `title`, `link`, `guid`, `description` (RSS HTML, if present), `published` (UTC ISO 8601 or null), `pub_date_raw`, `categories`, and `source_feeds`. Articles are sorted newest first. Matching article URLs are deduplicated across feeds after dropping query parameters, fragments and trailing slashes; the first copy's content is retained and all contributing feed URLs are collected in `source_feeds`. If there is no article URL, GUID or title/date is used instead. Only currently published items in each RSS feed can be retrieved; this is not a historical archive.

Feed failures are printed to stderr. If *some* feeds fail, the script writes the successful results, labels the output partial, and exits with code 2. If the directory cannot be downloaded or *all* feeds fail, it exits with code 1 without replacing any existing output. A complete run exits with code 0. It uses four concurrent requests by default, short retry backoffs and a 20-second per-request timeout; see `--help` to adjust them. `--page-file` accepts a locally saved directory HTML page for offline testing, but the feeds still require network access.

The GitHub Actions workflow can run the scraper from outside the sandbox and upload a JSONL artifact. RSS entries change as news is published, so rerunning the script will produce a different snapshot.
