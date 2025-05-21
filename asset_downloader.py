#!/usr/bin/env python3
"""
Downloads assets identified by main.py from assets.jsonl.
"""
import os
import time
import json
import hashlib
from urllib.parse import urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

# Load environment variables from .env (USER_AGENT expected)
load_dotenv()
USER_AGENT = os.getenv("USER_AGENT", "utc-asset-downloader/1.0")

# Paths and settings
BASE_DIR = "crawl_data"
ASSET_LOG_FILE = os.path.join(BASE_DIR, "assets.jsonl")
DOWNLOADED_ASSETS_LOG = os.path.join(BASE_DIR, "downloaded_assets.jsonl")
ASSET_ERROR_LOG = os.path.join(BASE_DIR, "asset_errors.jsonl")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
TIMEOUT = 10  # seconds for HTTP requests
RATE_LIMIT_SECONDS = 1 # seconds between requests

def ensure_directories_and_files():
    """Ensures that necessary directories and log files exist."""
    os.makedirs(ASSETS_DIR, exist_ok=True)
    for path in (DOWNLOADED_ASSETS_LOG, ASSET_ERROR_LOG):
        if not os.path.exists(path):
            open(path, "w", encoding="utf-8").close()

def load_already_downloaded():
    """Loads the set of URLs for assets already downloaded."""
    downloaded = set()
    if os.path.exists(DOWNLOADED_ASSETS_LOG):
        with open(DOWNLOADED_ASSETS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if "original_url" in entry:
                        downloaded.add(entry["original_url"])
                except json.JSONDecodeError:
                    continue
    return downloaded

def get_assets_to_download():
    """Reads assets.jsonl and returns a unique list of asset URLs to download."""
    assets_to_process = {} # Using dict to store asset_url -> first encountered page details
    if not os.path.exists(ASSET_LOG_FILE):
        print(f"Asset log file not found: {ASSET_LOG_FILE}")
        return []
    
    with open(ASSET_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                url = entry.get("url")
                if url and url not in assets_to_process:
                    assets_to_process[url] = entry # Store first encounter
            except json.JSONDecodeError:
                print(f"Skipping malformed line in {ASSET_LOG_FILE}: {line.strip()}")
                continue
    return list(assets_to_process.keys())


def download_asset(session, asset_url):
    """Downloads a single asset."""
    print(f"Processing asset: {asset_url}")
    time.sleep(RATE_LIMIT_SECONDS) # Respect rate limit
    try:
        response = session.get(asset_url, timeout=TIMEOUT, stream=True)
        response.raise_for_status() # Raises HTTPError for bad responses (4XX or 5XX)
        
        # Determine filename
        parsed_url = urlparse(asset_url)
        filename = os.path.basename(parsed_url.path)
        if not filename: # Handle cases like domain.com/ (though unlikely for assets)
            filename = hashlib.md5(asset_url.encode("utf-8")).hexdigest()

        # Ensure filename is not too long and has an extension if possible
        name, ext = os.path.splitext(filename)
        if not ext: # If no extension, try to get one from Content-Type or use a default
            content_type = response.headers.get('content-type')
            if content_type:
                import mimetypes
                ext_from_mime = mimetypes.guess_extension(content_type.split(';')[0])
                if ext_from_mime:
                    ext = ext_from_mime
        filename = f"{name[:100]}{ext}" # Limit name length, keep extension

        filepath = os.path.join(ASSETS_DIR, filename)

        # Check if file already exists locally (another form of re-download avoidance)
        # This is useful if downloaded_assets.jsonl was cleared but files remain
        if os.path.exists(filepath):
            # Simple check: if file exists, assume it's the same.
            # For robustness, a size or hash check could be added here.
            print(f"Asset file already exists: {filepath}. Skipping download.")
            # Log it as downloaded if not already, as we found the file
            return asset_url, filepath, True # True indicates it existed

        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"Successfully downloaded {asset_url} to {filepath}")
        return asset_url, filepath, False # False indicates it was newly downloaded
        
    except requests.exceptions.RequestException as e:
        print(f"Error downloading {asset_url}: {e}")
        with open(ASSET_ERROR_LOG, "a", encoding="utf-8") as ef:
            ef.write(json.dumps({
                "url": asset_url,
                "error": str(e),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")
            }) + "\n")
        return asset_url, None, False

def main():
    ensure_directories_and_files()

    # Setup requests session
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504], allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})

    already_downloaded_urls = load_already_downloaded()
    unique_asset_urls = get_assets_to_download()

    assets_to_download_count = 0
    for asset_url in unique_asset_urls:
        if asset_url not in already_downloaded_urls:
            assets_to_download_count +=1
            original_url, local_path, existed = download_asset(session, asset_url)
            if local_path: # If download (or local existence) was successful
                # Log to downloaded_assets.jsonl even if it just existed locally and wasn't in the log
                # This ensures consistency between the log and the file system for future runs.
                with open(DOWNLOADED_ASSETS_LOG, "a", encoding="utf-8") as df:
                    df.write(json.dumps({
                        "original_url": original_url,
                        "local_path": os.path.relpath(local_path, BASE_DIR), # Store relative path
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "existed_locally": existed
                    }) + "\n")
                # Add to set to prevent re-processing in this session if URL was duplicated in source somehow
                already_downloaded_urls.add(original_url) 
        else:
            print(f"Asset {asset_url} already processed or listed in {DOWNLOADED_ASSETS_LOG}. Skipping.")
            
    if assets_to_download_count == 0:
        print("No new assets to download.")
    print("Asset download process complete.")

if __name__ == "__main__":
    main()
