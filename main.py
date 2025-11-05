#!/usr/bin/env python3
"""
Polite breadth‑first crawler for https://utc.edu (or any single‑domain site)
--------------------------------------------------------------------------
Features
========
* **Resume reliably** – the frontier (remaining queue) is saved to `crawl_data/frontier.json` every 50 new pages and on graceful exit.
* **Duplicate logic fixed** – URLs are only marked *visited* after a successful download; content hash de‑duplication avoids re‑saving identical documents.
* **MAX_PAGES** – set via `MAX_PAGES` env. `0` (or unset) means unlimited.
* **Rich metadata** – title, h1, meta‑description, crawl timestamp, HTTP headers, outbound links, asset links, clean text, content hash.
* **Config via env / .env** – no CLI flags needed.

Environment variables (via `.env` or export)
--------------------------------------------
```
USER_AGENT="utc-crawler/1.0"
MAX_PAGES=100          # 0 = unlimited
SEED_URL="https://utc.edu"
DOMAIN="utc.edu"      # canonical domain to stay inside
RATE_LIMIT_SECONDS=1   # polite delay between requests
TIMEOUT=10             # HTTP timeout
```
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Set
from urllib.parse import (parse_qsl, urlencode, urljoin, urlparse,
                          urlunparse)

import requests
from bs4 import BeautifulSoup, Comment
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def canonicalise_url(url: str) -> str:
    """Return a canonicalised version of *url* suitable for de‑duplication."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    # Strip default ports
    if (scheme == "http" and netloc.endswith(":80")) or (
        scheme == "https" and netloc.endswith(":443")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    # Normalise path (collapse multiple slashes, remove trailing except root)
    path = re.sub(r"/+", "/", parsed.path)
    if len(path) > 1:
        path = path.rstrip("/")
    # Sort query params for stable ordering
    qs = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunparse((scheme, netloc, path, "", qs, ""))


def clean_text(html: str) -> str:
    """Remove boilerplate & return plain text suitable for embedding."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# Crawler class
# ---------------------------------------------------------------------------


class Crawler:
    def __init__(self) -> None:
        load_dotenv()
        # Config
        self.user_agent = os.getenv("USER_AGENT", "utc-crawler/1.0")
        self.seed_url = os.getenv("SEED_URL", "https://utc.edu")
        self.domain = os.getenv("DOMAIN", "utc.edu")
        self.max_pages = int(os.getenv("MAX_PAGES", "0")) or None  # None == unlimited
        self.rate_limit = float(os.getenv("RATE_LIMIT_SECONDS", "1"))
        self.timeout = float(os.getenv("TIMEOUT", "10"))

        # Paths
        self.base_dir = "crawl_data"
        self.pages_dir = os.path.join(self.base_dir, "pages")
        self.mapping_file = os.path.join(self.base_dir, "mapping.jsonl")
        self.error_file = os.path.join(self.base_dir, "errors.jsonl")
        self.frontier_file = os.path.join(self.base_dir, "frontier.json")

        os.makedirs(self.pages_dir, exist_ok=True)
        for p in (self.mapping_file, self.error_file):
            if not os.path.exists(p):
                open(p, "w").close()

        # State
        self.visited: Set[str] = set()
        self.queued: Set[str] = set()  # URLs currently in queue
        self.queue: Deque[str] = deque()
        self.seen_hashes: Set[str] = set()
        self.downloaded = 0

        self._load_previous_state()
        self._load_frontier()
        if not self.queue:
            self._enqueue(self.seed_url)

        # HTTP session with retries
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({"User-Agent": self.user_agent})

    # ---------------------------------------------------------------------
    # Persistence helpers
    # ---------------------------------------------------------------------

    def _load_previous_state(self) -> None:
        """Populate visited URLs and content hashes from previous runs."""
        if not os.path.exists(self.mapping_file):
            return
        with open(self.mapping_file, "r", encoding="utf-8") as mf:
            for line in mf:
                try:
                    rec = json.loads(line)
                    if rec.get("url"):
                        self.visited.add(rec["url"])
                    if rec.get("content_hash"):
                        self.seen_hashes.add(rec["content_hash"])
                except json.JSONDecodeError:
                    continue

    def _load_frontier(self) -> None:
        if not os.path.exists(self.frontier_file):
            return
        try:
            with open(self.frontier_file, "r", encoding="utf-8") as f:
                frontier = json.load(f)
            for url in frontier:
                self._enqueue(url)
        except Exception:
            # Corrupt frontier – start from scratch next run
            pass

    def _save_frontier(self) -> None:
        with open(self.frontier_file, "w", encoding="utf-8") as f:
            json.dump(list(self.queue), f)

    def _append_jsonl(self, path: str, obj: Dict) -> None:
        with open(path, "a", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.write("\n")

    # ---------------------------------------------------------------------
    # Queue helpers
    # ---------------------------------------------------------------------

    def _enqueue(self, url: str) -> None:
        canon = canonicalise_url(url)
        if canon in self.visited or canon in self.queued:
            return
        self.queue.append(canon)
        self.queued.add(canon)

    # ---------------------------------------------------------------------
    # Error counting (for summary)
    # ---------------------------------------------------------------------

    def _error_count(self) -> int:
        try:
            with open(self.error_file, "r", encoding="utf-8") as f:
                return sum(1 for _ in f if _.strip())
        except FileNotFoundError:
            return 0

    # ---------------------------------------------------------------------
    # Main crawl loop
    # ---------------------------------------------------------------------

    def crawl(self) -> None:
        print(f"Starting crawl – max_pages={self.max_pages or '∞'} | seed={self.seed_url}")
        try:
            while self.queue:
                if self.max_pages is not None and self.downloaded >= self.max_pages:
                    print("Reached MAX_PAGES limit – stopping.")
                    break

                url = self.queue.popleft()
                self.queued.discard(url)
                time.sleep(self.rate_limit)

                try:
                    resp = self.session.get(url, timeout=self.timeout)
                except Exception as exc:  # noqa: BLE001
                    error_msg = str(exc)
                    # Log the error with URL for debugging
                    self._append_jsonl(self.error_file, {"url": url, "error": error_msg})
                    print(f"Error fetching {url}: {error_msg}")
                    continue

                if resp.status_code != 200:
                    self._append_jsonl(
                        self.error_file,
                        {"url": url, "error": f"status_code {resp.status_code}"},
                    )
                    continue

                html = resp.text
                text = clean_text(html)
                content_hash = sha256(text)
                is_new_doc = content_hash not in self.seen_hashes

                # Only increase counters/save if it's genuinely new
                if is_new_doc:
                    self.seen_hashes.add(content_hash)
                    self.downloaded += 1

                soup = BeautifulSoup(html, "html.parser")
                title = soup.title.string.strip() if soup.title and soup.title.string else ""
                h1 = soup.h1.get_text(strip=True) if soup.h1 else ""
                meta_tag = soup.find("meta", attrs={"name": "description"})
                meta_description = meta_tag.get("content", "").strip() if meta_tag else ""

                # ----------------------------------------------------------------
                # Save new document (HTML + JSON metadata)
                # ----------------------------------------------------------------
                if is_new_doc:
                    html_name = f"{content_hash}.html"
                    html_path = os.path.join(self.pages_dir, html_name)
                    with open(html_path, "w", encoding="utf-8") as fh:
                        fh.write(html)

                    meta_path = os.path.join(self.pages_dir, f"{content_hash}.json")
                    metadata: Dict = {
                        "url": url,
                        "file": os.path.join("pages", html_name),
                        "crawl_ts": datetime.now(timezone.utc).isoformat(),
                        "status": resp.status_code,
                        "headers": {
                            "etag": resp.headers.get("ETag"),
                            "last_modified": resp.headers.get("Last-Modified"),
                            "content_type": resp.headers.get("Content-Type"),
                            "content_length": resp.headers.get("Content-Length"),
                        },
                        "title": title,
                        "h1": h1,
                        "meta_description": meta_description,
                        "content_hash": content_hash,
                        "links": [],
                        "assets": [],
                        "text": text,
                    }

                # ----------------------------------------------------------------
                # Link & asset extraction
                # ----------------------------------------------------------------
                for tag in soup.find_all("a", href=True):
                    href = tag["href"].strip()
                    try:
                        abs_url = canonicalise_url(urljoin(url, href))
                        parsed = urlparse(abs_url)
                    except (ValueError, Exception) as exc:
                        # Skip malformed URLs (e.g., invalid IPv6)
                        print(f"Skipping malformed URL '{href}' on {url}: {exc}")
                        continue

                    if parsed.scheme not in ("http", "https"):
                        continue
                    if parsed.netloc != self.domain:
                        continue

                    if abs_url.lower().endswith(
                        (".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".svg", ".pdf", ".zip", ".rar", ".ico")
                    ):
                        if is_new_doc:
                            metadata["assets"].append(abs_url)  # type: ignore[index]
                        continue

                    self._enqueue(abs_url)
                    if is_new_doc:
                        metadata["links"].append(abs_url)  # type: ignore[index]

                # Persist mapping & per-page JSON
                if is_new_doc:
                    with open(meta_path, "w", encoding="utf-8") as fm:
                        json.dump(metadata, fm, ensure_ascii=False, indent=2)

                    self._append_jsonl(
                        self.mapping_file,
                        {
                            "url": url,
                            "file": os.path.join("pages", html_name),
                            "title": title,
                            "content_hash": content_hash,
                        },
                    )

                # Mark as fully visited AFTER processing links
                self.visited.add(url)

                # Periodically checkpoint the frontier and progress
                if self.downloaded % 50 == 0 and self.downloaded:
                    self._save_frontier()
                    print(f"Checkpoint – downloaded {self.downloaded} pages, queue={len(self.queue)}")

        finally:
            # Always persist frontier on termination
            self._save_frontier()
            print(
                f"Crawl complete – downloaded {self.downloaded} new unique pages. "
                f"Errors logged: {self._error_count()}"
            )


# ---------------------------------------------------------------------------
# Entry‑point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    Crawler().crawl()
