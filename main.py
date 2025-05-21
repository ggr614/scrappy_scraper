#!/usr/bin/env python3
"""
Polite breadth-first web crawler for https://utc.edu
Saves each page's HTML and records URL→file→title mapping in JSONL files.
Also logs encountered asset links with their source pages (URL and title) for later retrieval.
Requirements:
  - Python 3.7+
  - python-dotenv
  - requests
  - beautifulsoup4
"""
import os
import time
import json
import hashlib
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Load environment variables from .env (USER_AGENT expected)
load_dotenv()
USER_AGENT = os.getenv("USER_AGENT", "utc-crawler/1.0")

# Crawl settings and paths
BASE_DIR          = "crawl_data"
PAGES_DIR         = os.path.join(BASE_DIR, "pages")
MAPPING_FILE      = os.path.join(BASE_DIR, "mapping.jsonl")
ERROR_FILE        = os.path.join(BASE_DIR, "errors.jsonl")
ASSET_FILE        = os.path.join(BASE_DIR, "assets.jsonl")
CHECKPOINT_FILE   = os.path.join(BASE_DIR, "checkpoint.json") # Added checkpoint file
SEED_URL          = "https://utc.edu"
DOMAIN            = "utc.edu"
RATE_LIMIT_SECONDS= 1
TIMEOUT           = 10  # seconds for HTTP requests

# Asset extensions to log
ASSET_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".css", ".js",
    ".svg", ".pdf", ".zip", ".rar", ".ico"
)

# Ensure directories and files exist
os.makedirs(PAGES_DIR, exist_ok=True)
for path in (MAPPING_FILE, ERROR_FILE, ASSET_FILE):
    if not os.path.exists(path):
        open(path, "w").close()

# Initialize 'seen' URLs from previous runs (if any)
seen = set()
for log_file in (MAPPING_FILE, ERROR_FILE):
    with open(log_file, "r", encoding="utf-8") as lf:
        for line in lf:
            try:
                entry = json.loads(line)
                url = entry.get("url")
                if url:
                    seen.add(url)
            except json.JSONDecodeError:
                continue

# Setup requests session with retry policy
session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET"]
)
adapter = HTTPAdapter(max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)
session.headers.update({"User-Agent": USER_AGENT})

# Initialize crawl queue
queue = deque()
checkpoint_loaded = False
if os.path.exists(CHECKPOINT_FILE):
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as cf:
            loaded_queue = json.load(cf)
            queue.extend(loaded_queue) # Load URLs into the deque
            for url_in_queue in loaded_queue: # Ensure all loaded URLs are in 'seen'
                seen.add(url_in_queue)
            print(f"Resuming crawl from checkpoint. {len(queue)} URLs loaded.")
            checkpoint_loaded = True
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading checkpoint file {CHECKPOINT_FILE}: {e}. Starting fresh crawl.")

if not checkpoint_loaded:
    if SEED_URL not in seen:
        queue.append(SEED_URL)
        seen.add(SEED_URL)
    print("Starting new crawl or resuming without checkpoint.")

# Crawling loop
PROCESSED_URLS_COUNT = 0 # Counter for periodic checkpoint saving
CHECKPOINT_INTERVAL = 50 # Save every 50 URLs

while queue:
    url = queue.popleft()
    PROCESSED_URLS_COUNT += 1
    print(f"Crawling: {url}")
    # Rate limit
    time.sleep(RATE_LIMIT_SECONDS)
    try:
        resp = session.get(url, timeout=TIMEOUT)
        status = resp.status_code
        if status == 200:
            html = resp.text
            # Parse the page
            soup = BeautifulSoup(html, "html.parser")
            # Extract title (if any)
            title_tag = soup.title.string.strip() if soup.title and soup.title.string else ""
            # Save HTML
            hash_name = hashlib.md5(url.encode("utf-8")).hexdigest() + ".html"
            rel_path = os.path.join("pages", hash_name)
            full_path = os.path.join(PAGES_DIR, hash_name) # Corrected: Use PAGES_DIR directly
            
            write_content = True
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f_old:
                    old_html = f_old.read()
                if old_html == html:
                    write_content = False
                    print(f"Content unchanged for {url}, not overwriting.") # Optional logging
            
            if write_content:
                file_existed_before_write = os.path.exists(full_path) # Check before writing
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(html)
                if not file_existed_before_write:
                     print(f"New file created for {url} at {full_path}")
                else:
                     print(f"File updated for {url} at {full_path}")
            
            # Record mapping (url, file, title)
            with open(MAPPING_FILE, "a", encoding="utf-8") as mf:
                mf.write(json.dumps({
                    "url": url,
                    "file": rel_path,
                    "title": title_tag
                }) + "\n")
            # Extract and process links
            for tag in soup.find_all("a", href=True):
                href = tag["href"].strip()
                absolute = urljoin(url, href)
                parsed = urlparse(absolute)
                # Only HTTP(s)
                if parsed.scheme not in ("http", "https"):
                    continue
                # Only same domain
                if parsed.netloc != DOMAIN:
                    continue
                # Strip fragments
                absolute = absolute.split("#")[0]
                # Asset link: log with page title and continue
                if absolute.lower().endswith(ASSET_EXTENSIONS):
                    with open(ASSET_FILE, "a", encoding="utf-8") as af:
                        af.write(json.dumps({
                            "url": absolute,
                            "page": url,
                            "title": title_tag
                        }) + "\n")
                    continue
                # Enqueue unseen URLs
                if absolute not in seen:
                    seen.add(absolute)
                    queue.append(absolute)
        else:
            # Log non-200 statuses
            with open(ERROR_FILE, "a", encoding="utf-8") as ef:
                ef.write(json.dumps({"url": url, "error": f"status_code {status}"}) + "\n")
    except Exception as e:
        # Log exceptions
        with open(ERROR_FILE, "a", encoding="utf-8") as ef:
            ef.write(json.dumps({"url": url, "error": str(e)}) + "\n")
    
    # Periodically save checkpoint
    if PROCESSED_URLS_COUNT % CHECKPOINT_INTERVAL == 0:
        try:
            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as cf:
                json.dump(list(queue), cf) # Save current queue state
            print(f"Checkpoint saved. {len(queue)} URLs in queue.")
        except IOError as e:
            print(f"Error saving checkpoint file {CHECKPOINT_FILE}: {e}")

# Crawl complete, clear checkpoint
if os.path.exists(CHECKPOINT_FILE):
    try:
        os.remove(CHECKPOINT_FILE)
        print(f"Crawl complete. Checkpoint file {CHECKPOINT_FILE} removed.")
    except OSError as e:
        print(f"Error removing checkpoint file {CHECKPOINT_FILE}: {e}")
else:
    print("Crawl complete.")
