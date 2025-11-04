#!/usr/bin/env python3
"""
PDF Asset Extractor
------------------
Analyzes crawled page JSON files and extracts all PDF assets into a JSONL file.

This script processes all JSON metadata files in the crawl_data/pages directory,
looks for assets ending in ".pdf", and accumulates them into a structured JSONL output.

Usage:
    python pdf_asset_extractor.py

Output:
    pdf_assets.jsonl - Contains one JSON record per PDF asset found
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set
from urllib.parse import urlparse


def extract_pdf_assets(pages_dir: str = "crawl_data/pages") -> List[Dict]:
    """
    Extract PDF assets from all JSON files in the pages directory.
    
    Args:
        pages_dir: Path to the directory containing JSON metadata files
        
    Returns:
        List of dictionaries containing PDF asset information
    """
    pdf_assets = []
    seen_pdfs: Set[str] = set()  # Track duplicates
    processed_files = 0
    
    pages_path = Path(pages_dir)
    if not pages_path.exists():
        print(f"Error: Directory '{pages_dir}' does not exist")
        return []
    
    # Process all JSON files in the pages directory
    json_files = list(pages_path.glob("*.json"))
    print(f"Processing {len(json_files)} JSON files...")
    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                page_data = json.load(f)
            
            processed_files += 1
            
            # Extract PDF assets from the current page
            assets = page_data.get("assets", [])
            for asset_url in assets:
                if asset_url.lower().endswith(".pdf"):
                    # Skip if we've already seen this PDF URL
                    if asset_url in seen_pdfs:
                        continue
                    
                    seen_pdfs.add(asset_url)
                    
                    # Extract filename from URL
                    parsed_url = urlparse(asset_url)
                    filename = os.path.basename(parsed_url.path) or "unknown.pdf"
                    
                    # Create PDF asset record
                    pdf_record = {
                        "pdf_url": asset_url,
                        "filename": filename,
                        "source_page_url": page_data.get("url", ""),
                        "source_page_title": page_data.get("title", ""),
                        "source_page_hash": page_data.get("content_hash", ""),
                        "discovered_at": page_data.get("crawl_ts", ""),
                        "extracted_at": datetime.now(timezone.utc).isoformat(),
                        "url_path": parsed_url.path,
                        "url_domain": parsed_url.netloc
                    }
                    
                    pdf_assets.append(pdf_record)
                    
        except (json.JSONDecodeError, FileNotFoundError, KeyError) as e:
            print(f"Warning: Error processing {json_file}: {e}")
            continue
    
    print(f"Processed {processed_files} JSON files")
    print(f"Found {len(pdf_assets)} unique PDF assets")
    
    return pdf_assets


def save_to_jsonl(pdf_assets: List[Dict], output_file: str = "pdf_assets.jsonl") -> None:
    """
    Save PDF assets to a JSONL file.
    
    Args:
        pdf_assets: List of PDF asset dictionaries
        output_file: Output filename for the JSONL file
    """
    with open(output_file, 'w', encoding='utf-8') as f:
        for asset in pdf_assets:
            json.dump(asset, f, ensure_ascii=False)
            f.write('\n')
    
    print(f"Saved {len(pdf_assets)} PDF assets to {output_file}")


def print_summary(pdf_assets: List[Dict]) -> None:
    """Print a summary of extracted PDF assets."""
    if not pdf_assets:
        print("No PDF assets found.")
        return
    
    # Count by domain
    domain_counts: Dict[str, int] = {}
    for asset in pdf_assets:
        domain = asset.get("url_domain", "unknown")
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    
    print(f"\n=== PDF Assets Summary ===")
    print(f"Total unique PDFs: {len(pdf_assets)}")
    print(f"\nPDFs by domain:")
    for domain, count in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {domain}: {count} PDFs")
    
    # Show first few examples
    print(f"\nFirst 5 PDFs found:")
    for i, asset in enumerate(pdf_assets[:5]):
        print(f"  {i+1}. {asset['filename']} - from {asset['source_page_title']}")


def main():
    """Main entry point."""
    print("UTC PDF Asset Extractor")
    print("=" * 50)
    
    # Check if pages directory exists
    pages_dir = "crawl_data/pages"
    if not os.path.exists(pages_dir):
        print(f"Error: Pages directory '{pages_dir}' not found.")
        print("Make sure you've run the crawler (scraperv2.py) first.")
        return
    
    # Extract PDF assets
    pdf_assets = extract_pdf_assets(pages_dir)
    
    if pdf_assets:
        # Save to JSONL
        save_to_jsonl(pdf_assets)
        
        # Print summary
        print_summary(pdf_assets)
    else:
        print("No PDF assets were found in the crawled pages.")


if __name__ == "__main__":
    main()