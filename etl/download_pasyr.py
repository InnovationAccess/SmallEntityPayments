#!/usr/bin/env python3
"""Download and parse all PASYR (Patent Assignment Annual) ZIP files.

Downloads each ZIP from the USPTO ODP API, parses the assignment XML inside,
and outputs a single consolidated gzipped JSONL file.

Usage:
    python download_pasyr.py <output_dir> [min_year]

Environment:
    USPTO_API_KEY - Required. ODP API key for authentication.

The script is resumable: it checks which files have already been parsed by
looking for marker files in the output directory.
"""

import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl.parse_assignments_xml_v2 import parse_input

API_BASE = "https://api.uspto.gov"
PRODUCT_ID = "PASYR"


def get_api_key():
    key = os.environ.get("USPTO_API_KEY")
    if not key:
        print("Error: USPTO_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return key


def list_files(api_key: str) -> list[dict]:
    """Get list of PASYR data files from ODP API."""
    url = f"{API_BASE}/api/v1/datasets/products/{PRODUCT_ID}"
    resp = requests.get(url, headers={"X-API-KEY": api_key})
    resp.raise_for_status()
    data = resp.json()
    product = data["bulkDataProductBag"][0]
    file_bag = product["productFileBag"]
    files = file_bag["fileDataBag"]
    # Only data ZIP files
    return [f for f in files if f["fileName"].endswith(".zip") and f["fileTypeText"] == "Data"]


def download_file(api_key: str, download_uri: str, output_path: str) -> bool:
    """Download a file from ODP using the signed CloudFront URL."""
    # Get the signed redirect URL
    resp = requests.get(download_uri, headers={"X-API-KEY": api_key}, allow_redirects=False)
    if resp.status_code != 302:
        print(f"  Error: expected 302, got {resp.status_code}", file=sys.stderr)
        return False

    signed_url = resp.headers.get("location")
    if not signed_url:
        print(f"  Error: no redirect URL in response", file=sys.stderr)
        return False

    # Download the actual file
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


def process_all(output_dir: str, min_year: int = 1980):
    """Download and parse all PASYR files."""
    api_key = get_api_key()
    os.makedirs(output_dir, exist_ok=True)

    print("Listing PASYR files...", file=sys.stderr)
    files = list_files(api_key)
    print(f"Found {len(files)} data files", file=sys.stderr)

    # Sort by filename for consistent ordering
    files.sort(key=lambda f: f["fileName"])

    # Track which files are already processed
    done_marker_dir = os.path.join(output_dir, ".done")
    os.makedirs(done_marker_dir, exist_ok=True)

    # Output file - append mode (each file's output goes to its own intermediate file)
    final_output = os.path.join(output_dir, "patent_assignments_v2.jsonl.gz")

    processed = 0
    skipped = 0
    total_rows = 0

    for i, f in enumerate(files):
        filename = f["fileName"]
        marker = os.path.join(done_marker_dir, filename + ".done")

        if os.path.exists(marker):
            print(f"[{i+1}/{len(files)}] Skipping {filename} (already processed)",
                  file=sys.stderr)
            skipped += 1
            continue

        print(f"\n[{i+1}/{len(files)}] Processing {filename} "
              f"({f['fileSize']/1024/1024:.0f} MB)...", file=sys.stderr)

        # Download to temp file
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if not download_file(api_key, f["fileDownloadURI"], tmp_path):
                print(f"  FAILED to download {filename}", file=sys.stderr)
                continue

            # Parse the ZIP
            intermediate = os.path.join(output_dir, f"pasyr_{filename}.jsonl.gz")
            count, skp = parse_input(tmp_path, intermediate, min_year)
            total_rows += count
            processed += 1

            # Mark as done
            with open(marker, "w") as m:
                m.write(f"{count}\n")

            print(f"  Done: {count:,} rows", file=sys.stderr)
        except Exception as e:
            print(f"  ERROR processing {filename}: {e}", file=sys.stderr)
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # Delay between downloads
        time.sleep(2)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"PASYR processing complete:", file=sys.stderr)
    print(f"  Files processed: {processed}", file=sys.stderr)
    print(f"  Files skipped (already done): {skipped}", file=sys.stderr)
    print(f"  Total rows: {total_rows:,}", file=sys.stderr)
    print(f"  Output directory: {output_dir}", file=sys.stderr)

    return total_rows


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <output_dir> [min_year]")
        sys.exit(1)

    output_dir = sys.argv[1]
    min_year = int(sys.argv[2]) if len(sys.argv) > 2 else 1980

    process_all(output_dir, min_year)
