#!/usr/bin/env python3
"""Download and parse all PTBLXML (Patent Grant Bibliographic XML) files for forward citations.

Downloads weekly ZIP files from the USPTO ODP API, parses the XML inside
for US patent-to-patent citation relationships, and outputs gzipped JSONL files.

Usage:
    python download_ptblxml.py <output_dir> [start_year]

Environment:
    USPTO_API_KEY - Required. ODP API key for authentication.

The script is resumable: it checks which files have already been parsed by
looking for marker files in the output directory.
"""

import gzip
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl.parse_ptblxml import parse_zip

API_BASE = "https://api.uspto.gov"
PRODUCT_ID = "PTBLXML"


def get_api_key():
    key = os.environ.get("USPTO_API_KEY")
    if not key:
        print("Error: USPTO_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return key


def list_files(api_key: str, start_year: int = 2002) -> list[dict]:
    """Get list of PTBLXML data files from ODP API."""
    url = f"{API_BASE}/api/v1/datasets/products/{PRODUCT_ID}"
    resp = requests.get(url, headers={"X-API-KEY": api_key})
    resp.raise_for_status()
    data = resp.json()
    product = data["bulkDataProductBag"][0]
    file_bag = product["productFileBag"]
    files = file_bag["fileDataBag"]

    # Only weekly data ZIP files, filtered by start_year
    # Skip annual files (e.g., 2006_xml.zip) — they are multi-GB and crash the parser
    # Weekly files are named like ipgb20260303_wk09.zip
    result = []
    for f in files:
        if not f["fileName"].endswith(".zip"):
            continue
        if f.get("fileTypeText") != "Data":
            continue
        # Only process weekly files (ipgbYYYYMMDD_wkNN.zip)
        match = re.search(r"ipgb(\d{4})", f["fileName"])
        if not match:
            # Skip annual files (2002_xml.zip, etc.) - too large for in-memory XML
            continue
        year = int(match.group(1))
        if year < start_year:
            continue
        result.append(f)

    return result


def download_file(api_key: str, download_uri: str, output_path: str) -> bool:
    """Download a file from ODP using the signed CloudFront URL."""
    resp = requests.get(download_uri, headers={"X-API-KEY": api_key}, allow_redirects=False)
    if resp.status_code != 302:
        print(f"  Error: expected 302, got {resp.status_code}", file=sys.stderr)
        return False

    signed_url = resp.headers.get("location")
    if not signed_url:
        print(f"  Error: no redirect URL in response", file=sys.stderr)
        return False

    dl = requests.get(signed_url, stream=True)
    if dl.status_code != 200:
        print(f"  Error: download returned {dl.status_code}", file=sys.stderr)
        return False

    content_type = dl.headers.get("content-type", "")
    if "html" in content_type:
        print(f"  Error: got HTML instead of ZIP (content-type: {content_type})",
              file=sys.stderr)
        return False

    total = 0
    with open(output_path, "wb") as f:
        for chunk in dl.iter_content(chunk_size=65536):
            f.write(chunk)
            total += len(chunk)

    print(f"  Downloaded {total / 1024 / 1024:.1f} MB", file=sys.stderr)
    return True


def process_all(output_dir: str, start_year: int = 2002):
    """Download and parse all PTBLXML files."""
    api_key = get_api_key()
    os.makedirs(output_dir, exist_ok=True)

    print("Listing PTBLXML files...", file=sys.stderr)
    files = list_files(api_key, start_year)
    # Sort by date (oldest first for backfill)
    files.sort(key=lambda f: f["fileName"])
    print(f"Found {len(files)} data files from {start_year} onward", file=sys.stderr)

    total_size = sum(f["fileSize"] for f in files)
    print(f"Total download size: {total_size/1024/1024/1024:.1f} GB", file=sys.stderr)

    done_marker_dir = os.path.join(output_dir, ".done")
    os.makedirs(done_marker_dir, exist_ok=True)

    processed = 0
    skipped = 0
    total_rows = 0

    for i, f in enumerate(files):
        filename = f["fileName"]
        marker = os.path.join(done_marker_dir, filename + ".done")

        if os.path.exists(marker):
            skipped += 1
            continue

        print(f"\n[{i+1}/{len(files)}] Processing {filename} "
              f"({f['fileSize']/1024/1024:.0f} MB)...", file=sys.stderr)

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if not download_file(api_key, f["fileDownloadURI"], tmp_path):
                print(f"  FAILED to download {filename}", file=sys.stderr)
                continue

            # Parse the ZIP
            jsonl_path = os.path.join(output_dir, f"citations_{filename}.jsonl.gz")
            count, skp = parse_zip(tmp_path, jsonl_path)
            total_rows += count
            processed += 1

            with open(marker, "w") as m:
                m.write(f"{count}\n")

            print(f"  Done: {count:,} citation rows", file=sys.stderr)
        except Exception as e:
            print(f"  ERROR processing {filename}: {e}", file=sys.stderr)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        time.sleep(2)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"PTBLXML processing complete:", file=sys.stderr)
    print(f"  Files processed: {processed}", file=sys.stderr)
    print(f"  Files skipped (already done): {skipped}", file=sys.stderr)
    print(f"  Total citation rows: {total_rows:,}", file=sys.stderr)
    print(f"  Output directory: {output_dir}", file=sys.stderr)

    return total_rows


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <output_dir> [start_year]")
        sys.exit(1)

    output_dir = sys.argv[1]
    start_year = int(sys.argv[2]) if len(sys.argv) > 2 else 2002

    process_all(output_dir, start_year)
