## Scrappy Scraper 

## Version 1.0

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import json
import time

# Headers with User-Agent
headers = {
    "User-Agent": "ChattUTC/1.0"
}

# Track URLs
visited = set()
unvisited = set(["https://www.utc.edu"])
MAX_PAGES = 20

# Output file
OUTPUT_FILE = "utc_scrape.jsonl"

def fetch_page(url):
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.text
    except requests.RequestException as e:
        print(f"Failed to fetch {url}: {e}")
    return None

def extract_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        full_url = urljoin(base_url, href)
        if "utc.edu" in urlparse(full_url).netloc:
            links.add(full_url)
    return links

def extract_data(html, url):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title else "No Title"
    return {
        "url": url,
        "title": title
    }

def save_to_jsonl(data, filename):
    with open(filename, "a", encoding="utf-8") as f:
        json.dump(data, f)
        f.write("\n")

# Main loop
while unvisited and len(visited) < MAX_PAGES:
    current_url = unvisited.pop()
    if current_url in visited:
        continue

    print(f"Crawling: {current_url}")
    time.sleep(1)
    html = fetch_page(current_url)
    if html:
        visited.add(current_url)
        data = extract_data(html, current_url)
        save_to_jsonl(data, OUTPUT_FILE)

        new_links = extract_links(html, current_url)
        unvisited.update(new_links - visited)

print(f"\nScraping complete. Total pages saved: {len(visited)}")

