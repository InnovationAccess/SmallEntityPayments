#!/usr/bin/env python3
"""Download and parse PASDL (Patent Assignment Daily) ZIP files.

Downloads daily update files from the USPTO ODP API, parses the assignment XML,
and outputs gzipped JSONL files ready for BigQuery loading.

Usage:
    python download_pasdl.py <output_dir> [min_year] [--recent N]

Options:
    output_dir  Directory for output JSONL files and .done markers
    min_year    Skip assignments recorded before this year (default: 2006)
    --recent N  Only process the N most recent files (default: all)

Environment:
    USPTO_API_KEY - Required. ODP API key for authentication.

The script is resumable: it checks which files have already been parsed by
looking for marker files in the output directory.
"""

import os
import re
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl.parse_assignments_xml_v4 import parse_input

API_BASE = "https://api.uspto.gov"
PRODUCT_ID = "PASDL"


def get_api_key():
    key = os.environ.get("USPTO_API_KEY")
    if not key:
        print("Error: USPTO_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return key


def list_files(api_key: str) -> list[dict]:
    """Get list of PASDL data files from ODP API."""
    url = f"{API_BASE}/api/v1/datasets/products/{PRODUCT_ID}"
    resp = requests.get(url, headers={"X-API-KEY": api_key})
    resp.raise_for_status()
    data = resp.json()
    product = data["bulkDataProductBag"][0]
    file_bag = product["productFileBag"]
    files = file_bag["fileDataBag"]
    return [f for f in files if f["fileName"].endswith(".zip") and f["fileTypeText"] == "Data"]


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

    total = 0
    with open(output_path, "wb") as f:
        for chunk in dl.iter_content(chunk_size=65536):
            f.write(chunk)
            total += len(chunk)

    print(f"  Downloaded {total / 1024 / 1024:.1f} MB", file=sys.stderr)
    return True


def process_all(output_dir: str, min_year: int = 2006, recent: int | None = None):
    """Download and parse PASDL files."""
    api_key = get_api_key()
    os.makedirs(output_dir, exist_ok=True)

    print("Listing PASDL files...", file=sys.stderr)
    files = list_files(api_key)
    files.sort(key=lambda f: f["fileName"])
    print(f"Found {len(files)} data files", file=sys.stderr)

    # If --recent specified, only process the most recent N files
    if recent and recent < len(files):
        files = files[-recent:]
        print(f"Processing only the {recent} most recent files", file=sys.stderr)

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
              f"({f['fileSize']/1024/1024:.1f} MB)...", file=sys.stderr)

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if not download_file(api_key, f["fileDownloadURI"], tmp_path):
                print(f"  FAILED to download {filename}", file=sys.stderr)
                continue

            parse_dir = os.path.join(output_dir, f"pasdl_{filename}")
            counts = parse_input(tmp_path, parse_dir, min_year)
            total = sum(counts.values())
            total_rows += total
            processed += 1

            with open(marker, "w") as m:
                m.write(f"{total}\n")

            print(f"  Done: {counts['records']:,} records, {counts['documents']:,} documents",
                  file=sys.stderr)
        except Exception as e:
            print(f"  ERROR processing {filename}: {e}", file=sys.stderr)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        time.sleep(2)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"PASDL processing complete:", file=sys.stderr)
    print(f"  Files processed: {processed}", file=sys.stderr)
    print(f"  Files skipped (already done): {skipped}", file=sys.stderr)
    print(f"  Total rows: {total_rows:,}", file=sys.stderr)
    print(f"  Output directory: {output_dir}", file=sys.stderr)

    return total_rows


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <output_dir> [min_year] [--recent N]")
        sys.exit(1)

    output_dir = sys.argv[1]
    min_year = 2006
    recent = None

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--recent" and i + 1 < len(args):
            recent = int(args[i + 1])
            i += 2
        else:
            min_year = int(args[i])
            i += 1

    process_all(output_dir, min_year, recent)
