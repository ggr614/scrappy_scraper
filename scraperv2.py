#!/usr/bin/env python3
"""
Polite breadth-first crawler for https://utc.edu

Key features added
------------------
* MAX_PAGES support via environment variable (set MAX_PAGES in .env or the environment)
* Encapsulated in a `Crawler` class - no global state
* Canonical URL normalisation to avoid duplicate crawl targets
* De-duplication by **content hash** (only unique documents are stored)
* Rich metadata captured (crawl timestamp, HTTP headers, ETag, Last-Modified)
* Clean main-text extraction stored alongside full HTML
* Structured per-page metadata file: `pages/<sha256>.json`
* Mapping file (mapping.jsonl) still created for easy grepping
"""
import os
import time
import json
import hashlib
import re
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
from typing import Optional, Set
from urllib.parse import (
    urljoin,
    urlparse,
    urlunparse,
    urlencode,
    parse_qsl,
)

import requests
from bs4 import BeautifulSoup, Comment
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class Crawler:
    """Polite breadth-first crawler limited to a single domain."""

    # File/directory layout (relative to base_dir)
    PAGES_SUBDIR = "pages"
    MAPPING_FILE = "mapping.jsonl"
    ERROR_FILE = "errors.jsonl"
    ASSET_FILE = "assets.jsonl"

    # Extensions regarded as assets (not HTML documents)
    ASSET_EXTENSIONS = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".css",
        ".js",
        ".svg",
        ".pdf",
        ".zip",
        ".rar",
        ".ico",
    )

    def __init__(
        self,
        seed_url: str,
        domain: str,
        *,
        base_dir: str = "crawl_data",
        rate_limit: float = 1.0,  # seconds between requests
        timeout: int = 10,
        max_retries: int = 3,
    ):
        load_dotenv()
        self.user_agent = os.getenv("USER_AGENT", "utc-crawler/2.0")
        # Max pages is taken from env; 0 or unset means unlimited
        self.max_pages: Optional[int] = (
            int(os.getenv("MAX_PAGES", "0")) or None
        )

        self.seed_url = seed_url.rstrip("/")
        self.domain = domain.lower()
        self.base_dir = Path(base_dir)
        self.pages_dir = self.base_dir / self.PAGES_SUBDIR
        self.rate_limit = rate_limit
        self.timeout = timeout

        # Create folders / files
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        for fname in (self.MAPPING_FILE, self.ERROR_FILE, self.ASSET_FILE):
            fpath = self.base_dir / fname
            if not fpath.exists():
                fpath.touch()

        # Crawler state
        self.seen_urls: Set[str] = set()
        self.seen_hashes: Set[str] = set()
        self.queue: deque[str] = deque()
        self.downloaded = 0

        # Load previous crawl to resume intelligently
        self._load_previous_state()
        if self._canonicalise(seed_url) not in self.seen_urls:
            self.queue.append(seed_url)
            self.seen_urls.add(self._canonicalise(seed_url))

        # HTTP session with retries
        self.session = requests.Session()
        retries = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({"User-Agent": self.user_agent})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self):
        print(
            f"\nStarting crawl - seed: {self.seed_url} - max_pages: {self.max_pages or '∞'}\n"
        )
        while self.queue and not self._max_reached():
            url = self.queue.popleft()
            time.sleep(self.rate_limit)
            self._crawl_page(url)
        print("\nCrawl complete. Downloaded", self.downloaded, "unique pages.\n")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_previous_state(self):
        """Populate seen_urls and seen_hashes from prior crawl for resumability."""
        mapping_path = self.base_dir / self.MAPPING_FILE
        if not mapping_path.exists():
            return
        with mapping_path.open("r", encoding="utf-8") as mp:
            for line in mp:
                try:
                    entry = json.loads(line)
                    url = entry.get("url")
                    h = entry.get("content_hash")
                    if url:
                        self.seen_urls.add(self._canonicalise(url))
                    if h:
                        self.seen_hashes.add(h)
                except json.JSONDecodeError:
                    continue

    def _canonicalise(self, url: str) -> str:
        """Return a deterministic canonical form of the URL for de-duplication."""
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        # Remove default ports
        if (scheme == "http" and netloc.endswith(":80")) or (
            scheme == "https" and netloc.endswith(":443")
        ):
            netloc = netloc.split(":")[0]
        # Sort query params alphabetically to avoid ?a=1&b=2 vs ?b=2&a=1
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        # Remove fragments
        sanitized = parsed._replace(fragment="", query=query, netloc=netloc)
        # Normalise path: remove double slashes and trailing slash (except root)
        path = re.sub(r"//+", "/", sanitized.path)
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        sanitized = sanitized._replace(path=path)
        return urlunparse(sanitized)

    def _clean_text(self, soup: BeautifulSoup) -> str:
        """Return main visible text - cheap heuristic (not full readability)."""
        # Remove script, style, noscript, and comments
        for elem in soup(["script", "style", "noscript"]):
            elem.decompose()
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()
        text = soup.get_text(separator=" ", strip=True)
        # Collapse multiple whitespace
        text = re.sub(r"\s+", " ", text)
        return text

    def _max_reached(self) -> bool:
        return self.max_pages is not None and self.downloaded >= self.max_pages

    def _crawl_page(self, url: str):
        canonical_url = self._canonicalise(url)
        try:
            resp = self.session.get(canonical_url, timeout=self.timeout)
        except Exception as exc:
            self._log_error(canonical_url, str(exc))
            return

        status = resp.status_code
        if status != 200 or "text/html" not in resp.headers.get("Content-Type", ""):
            # Non-HTML or error → log and bail
            self._log_error(canonical_url, f"status {status} / content-type {resp.headers.get('Content-Type')}")
            return

        soup = BeautifulSoup(resp.text, "html.parser")
        clean_text = self._clean_text(soup)
        content_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
        if content_hash in self.seen_hashes:
            # Duplicate content - still mark as seen to avoid re-visiting links
            self._enqueue_links(soup, canonical_url, title=self._get_title(soup))
            return

        # Unique document → persist
        self.seen_hashes.add(content_hash)
        self.downloaded += 1

        title = self._get_title(soup)
        timestamp = datetime.now(timezone.utc).isoformat()
        # Persist HTML
        html_fname = f"{content_hash}.html"
        html_rel_path = f"{self.PAGES_SUBDIR}/{html_fname}"
        html_abs_path = self.base_dir / html_rel_path
        html_abs_path.write_text(resp.text, encoding="utf-8")

        # Compose metadata dict
        meta = {
            "url": canonical_url,
            "status": status,
            "crawl_ts": timestamp,
            "headers": {
                "content_type": resp.headers.get("Content-Type"),
                "last_modified": resp.headers.get("Last-Modified"),
                "etag": resp.headers.get("ETag"),
                "content_length": resp.headers.get("Content-Length"),
            },
            "title": title,
            "content_hash": content_hash,
            "html_file": html_rel_path,
            "text": clean_text,
        }

        # Write per-page metadata json
        json_path = self.pages_dir / f"{content_hash}.json"
        json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # Append to mapping file (for quick grep / resume)
        mapping_line = {
            "url": canonical_url,
            "file": html_rel_path,
            "title": title,
            "content_hash": content_hash,
        }
        with (self.base_dir / self.MAPPING_FILE).open("a", encoding="utf-8") as mf:
            mf.write(json.dumps(mapping_line, ensure_ascii=False) + "\n")

        # Extract and enqueue links
        self._enqueue_links(soup, canonical_url, title)

        # Report progress every 50 pages
        if self.downloaded % 50 == 0:
            print(
                f"{self.downloaded} pages saved · queue:{len(self.queue)} · seen:{len(self.seen_urls)}"
            )

    def _enqueue_links(self, soup: BeautifulSoup, page_url: str, title: str):
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            absolute = urljoin(page_url, href)
            canonical_link = self._canonicalise(absolute)
            parsed = urlparse(canonical_link)

            # Stay within domain and http(s)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc != self.domain:
                continue

            # Asset link → log and continue
            if canonical_link.lower().endswith(self.ASSET_EXTENSIONS):
                self._log_asset(canonical_link, page_url, title)
                continue

            # Enqueue unseen
            if canonical_link not in self.seen_urls:
                self.seen_urls.add(canonical_link)
                self.queue.append(canonical_link)

    # ------------------------------ Log helpers ------------------------------
    def _log_error(self, url: str, message: str):
        err_path = self.base_dir / self.ERROR_FILE
        with err_path.open("a", encoding="utf-8") as ef:
            ef.write(json.dumps({"url": url, "error": message}) + "\n")

    def _log_asset(self, asset_url: str, page_url: str, title: str):
        asset_path = self.base_dir / self.ASSET_FILE
        with asset_path.open("a", encoding="utf-8") as af:
            af.write(
                json.dumps({"url": asset_url, "page": page_url, "title": title}) + "\n"
            )

    @staticmethod
    def _get_title(soup: BeautifulSoup) -> str:
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return ""


# ---------------------------------------------------------------------------
# Main entrypoint - nothing fancy so it can be scheduled easily (cron, etc.)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    SEED_URL = os.getenv("SEED_URL", "https://utc.edu")
    DOMAIN = urlparse(SEED_URL).netloc.lower().lstrip("www.") or "utc.edu"

    crawler = Crawler(
        seed_url=SEED_URL,
        domain=DOMAIN,
        base_dir=os.getenv("BASE_DIR", "crawl_data"),
        rate_limit=float(os.getenv("RATE_LIMIT_SECONDS", "1")),
        timeout=int(os.getenv("HTTP_TIMEOUT", "10")),
    )
    crawler.run()
