# UTC Polite Crawler

A restart‑friendly breadth‑first web crawler tailored for [https://utc.edu](https://utc.edu) (or any single‑domain site).
It collects page **HTML**, a boiler‑plate–stripped **clean text**, and rich **metadata** ready for vector‑DB
workflows (e.g. retrieval‑augmented generation).

---

## Features

| Capability                              | Notes                                                                                                                          |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **MAX\_PAGES guard**                    | Stop after *N* unique pages (set via env var).                                                                                 |
| **Resume support**                      | Frontier is checkpointed ⇒ reruns pick up where they left off.                                                                 |
| **Canonical URL + content‑hash de‑dup** | Prevents duplicate downloads and duplicate embeddings.                                                                         |
| **Rich metadata**                       | `etag`, `last_modified`, headers, `<title>`, `<h1>`, description, outbound links, asset links, language, SHA‑256 content hash. |
| **Polite crawling**                     | Rate‑limit, retry/back‑off, optional `robots.txt` respect (toggle).                                                            |
| **Clean text extraction**               | Strips scripts/ads/nav so text is RAG‑ready.                                                                                   |
| **Structured output**                   | JSON per page plus mapping & error logs for quick analysis.                                                                    |

---

## Directory layout

```
crawl_data/
├─ pages/                # Raw HTML (md5(url).html)
├─ json/                 # Rich per‑page JSON (sha256(text).json)
├─ mapping.jsonl         # url → {html, json, title}
├─ assets.jsonl          # static asset links discovered
├─ errors.jsonl          # failed fetches / status≠200 / exceptions
├─ frontier.json         # queue checkpoint for resuming
└─ seen.txt              # canonical URLs already downloaded (one per line)
```

> **Tip**
> Feed the `json/` files straight to your embedding pipeline; they already include `clean_text` and metadata.

---

## Requirements

* Python 3.8+
* [`pip install -r requirements.txt`](#requirements-txt)

```text
beautifulsoup4
python-dotenv
requests
urllib3
```

Optionally for better text extraction:

```text
trafilatura  # drop‑in replacement for clean_text()
```

---

## Configuration via environment variables

Create a `.env` in the project root or export variables in your shell.

| Variable         | Default           | Purpose                                         |
| ---------------- | ----------------- | ----------------------------------------------- |
| `USER_AGENT`     | `utc-crawler/1.0` | Sent with every request.                        |
| `SEED_URL`       | `https://utc.edu` | Start URL. Must belong to `DOMAIN`.             |
| `DOMAIN`         | `utc.edu`         | Allowed hostname (no sub‑domains outside this). |
| `MAX_PAGES`      | `0` (unlimited)   | Stop after N unique pages.                      |
| `RATE_LIMIT`     | `1.0` sec         | Delay between requests.                         |
| `TIMEOUT`        | `10` sec          | Per‑request timeout.                            |
| `BASE_DIR`       | `crawl_data`      | Root output folder.                             |
| `RESPECT_ROBOTS` | `false`           | Set to `true` to obey `robots.txt`.             |

Example `.env`:

```dotenv
USER_AGENT=MyUniversityCrawler/0.2
MAX_PAGES=500
RATE_LIMIT=0.5
```

---

## Running the crawler

```bash
# 1) Activate venv & load env vars
source .venv/bin/activate  # or however you manage venvs

# 2) Start crawl (will resume if frontier.json exists)
python utc_crawler.py
```

On first run you’ll see:

```
Starting crawl – max_pages=500 | seed=https://utc.edu
...
Crawl complete – downloaded 312 new unique pages. Errors logged: 4
```

Stopping the script (Ctrl‑C) or reaching `MAX_PAGES` writes a final frontier
checkpoint. Subsequent runs continue until *all* reachable pages are fetched or the
new limit is hit.

---

## Integrating with a RAG pipeline

1. **Chunk & embed** – Iterate over `crawl_data/json/*.json`, take the `clean_text`
   (already boiler‑plate–free) and chunk 200‑500 tokens with overlap.
2. **Store** chunks, embeddings, and metadata (`url`, `title`, `h1`, etc.) in
   `pgvector`, `qdrant`, or similar.
3. **Retrieve + generate** – Use hybrid BM25+vector retrieval with path‑prefix boosting
   to answer questions grounded in UTC web content.

---

## Troubleshooting

| Symptom                                                                | Fix                                                                                                                                           |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| “Crawl complete – downloaded 0 new unique pages” but you expected more | Check that `frontier.json` isn’t empty and `MAX_PAGES` isn’t already reached. Delete `frontier.json` and `seen.txt` to force a full restart.  |
| 429 / too many requests                                                | Increase `RATE_LIMIT`, lower concurrency (if you added async).                                                                                |
| Non‑HTML responses stored                                              | Crawler filters by `Content‑Type` but if your site serves HTML with a non‑standard header, add it to the allow‑list in `_is_html_response()`. |

---

## Roadmap / TODO

* Optional **asyncio + httpx** client for higher throughput.
* **Sitemap.xml** seeding.
* Incremental re‑crawl logic using `etag` / `last_modified`.
* Pluggable **boiler‑plate removal** (readability, trafilatura…).

Contributions & suggestions welcome — open an issue or PR! \:rocket:

---

## License

APACHE – see `LICENSE` for details.
