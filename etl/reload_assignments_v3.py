#!/usr/bin/env python3
"""Reload patent_assignments_v3 from the PASYR annual backfile.

Downloads the most recent PASYR backfile from the USPTO ODP API,
parses each shard with the v3 parser, uploads JSONL.gz to GCS,
and loads into BigQuery.

Uses only the most recent backfile release (ad19880101-20251231-*)
to avoid duplicates from overlapping releases.

Usage:
    python reload_assignments_v3.py [--min-year 1980] [--dry-run]

Environment:
    USPTO_API_KEY     - Required for ODP API authentication
    GCP_PROJECT_ID    - BigQuery project (default: uspto-data-app)
    BIGQUERY_DATASET  - BigQuery dataset (default: uspto_data)
    GCS_BUCKET        - GCS bucket for staging (default: uspto-bulk-staging)
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl.parse_assignments_xml_v3 import parse_input

API_BASE = "https://api.uspto.gov"
PRODUCT_ID = "PASYR"

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "uspto-data-app")
BQ_DATASET = os.environ.get("BIGQUERY_DATASET", "uspto_data")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "uspto-bulk-staging")
BQ_LOCATION = "us-west1"
BQ_TABLE = "patent_assignments_v3"
GCS_DIR = "v3/pasyr"


def get_api_key():
    key = os.environ.get("USPTO_API_KEY")
    if not key:
        print("Error: USPTO_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return key


def list_pasyr_files(api_key: str) -> list[dict]:
    """Get list of PASYR backfile ZIPs from ODP API."""
    url = f"{API_BASE}/api/v1/datasets/products/{PRODUCT_ID}"
    resp = requests.get(url, headers={"X-API-KEY": api_key})
    resp.raise_for_status()
    data = resp.json()
    product = data["bulkDataProductBag"][0]
    files = product["productFileBag"]["fileDataBag"]
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


def gsutil_upload(local_path: str, gcs_path: str) -> bool:
    """Upload a single file to GCS."""
    result = subprocess.run(
        ["gsutil", "cp", local_path, gcs_path],
        capture_output=True, text=True, timeout=3600
    )
    if result.returncode != 0:
        print(f"  Upload error: {result.stderr[:500]}", file=sys.stderr)
    return result.returncode == 0


def bq_load(gcs_path: str) -> bool:
    """Load a JSONL.gz file into BigQuery."""
    full_table = f"{GCP_PROJECT}:{BQ_DATASET}.{BQ_TABLE}"
    result = subprocess.run(
        ["bq", "load",
         f"--project_id={GCP_PROJECT}",
         f"--location={BQ_LOCATION}",
         "--source_format=NEWLINE_DELIMITED_JSON",
         full_table, gcs_path],
        capture_output=True, text=True, timeout=3600
    )
    if result.returncode != 0:
        print(f"  BQ load error: {result.stderr[:500]}", file=sys.stderr)
    return result.returncode == 0


def select_most_recent_backfile(files: list[dict]) -> list[dict]:
    """Select only the most recent backfile release.

    PASYR has multiple overlapping releases (e.g. 1980-2024, 1988-2024, 1988-2025).
    We want only the most recent to avoid duplicate assignments.
    """
    # Group by prefix pattern (date range before shard number)
    prefixes = {}
    for f in files:
        name = f["fileName"]
        # Pattern: adYYYYMMDD-YYYYMMDD-NN.zip
        parts = name.rsplit("-", 1)
        if len(parts) == 2:
            prefix = parts[0]  # e.g. "ad19880101-20251231"
            prefixes.setdefault(prefix, []).append(f)

    if not prefixes:
        return files

    # Pick the most recent prefix (highest end date)
    latest_prefix = max(prefixes.keys())
    selected = prefixes[latest_prefix]
    selected.sort(key=lambda f: f["fileName"])

    print(f"Selected backfile release: {latest_prefix} ({len(selected)} shards)", file=sys.stderr)
    other_count = len(files) - len(selected)
    if other_count > 0:
        print(f"Skipping {other_count} files from older releases", file=sys.stderr)

    return selected


def main():
    min_year = 1980
    dry_run = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--min-year" and i + 1 < len(args):
            min_year = int(args[i + 1])
            i += 2
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            print(f"Unknown argument: {args[i]}")
            sys.exit(1)

    api_key = get_api_key()

    print("Listing PASYR backfile files...", file=sys.stderr)
    all_files = list_pasyr_files(api_key)
    print(f"Found {len(all_files)} total PASYR files", file=sys.stderr)

    files = select_most_recent_backfile(all_files)

    if dry_run:
        print("\n[DRY RUN] Would process these files:", file=sys.stderr)
        for f in files:
            print(f"  {f['fileName']}  ({f['fileSize']/1024/1024:.0f} MB)", file=sys.stderr)
        total_mb = sum(f['fileSize'] for f in files) / 1024 / 1024
        print(f"\nTotal download: {total_mb:.0f} MB ({total_mb/1024:.1f} GB)", file=sys.stderr)
        return

    # Check for done markers to support resumability
    done_dir = "/tmp/reload-v3/.done"
    os.makedirs(done_dir, exist_ok=True)

    total_rows = 0
    processed = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    for i, f in enumerate(files):
        filename = f["fileName"]
        marker = os.path.join(done_dir, filename + ".done")

        if os.path.exists(marker):
            print(f"[{i+1}/{len(files)}] {filename} — already done, skipping", file=sys.stderr)
            skipped += 1
            continue

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[{i+1}/{len(files)}] Processing {filename} ({f['fileSize']/1024/1024:.0f} MB)...",
              file=sys.stderr)

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Download
            if not download_file(api_key, f["fileDownloadURI"], tmp_path):
                print(f"  FAILED to download {filename}", file=sys.stderr)
                failed += 1
                continue

            # Parse with v3 parser
            jsonl_path = f"/tmp/reload-v3/pasyr_{filename}.jsonl.gz"
            os.makedirs(os.path.dirname(jsonl_path), exist_ok=True)
            count, skp = parse_input(tmp_path, jsonl_path, min_year)
            print(f"  Parsed {count:,} rows ({skp:,} assignments skipped)", file=sys.stderr)

            if count == 0:
                print(f"  No rows — skipping upload", file=sys.stderr)
                with open(marker, "w") as m:
                    m.write("0\n")
                continue

            # Upload to GCS
            gcs_path = f"gs://{GCS_BUCKET}/{GCS_DIR}/pasyr_{filename}.jsonl.gz"
            print(f"  Uploading to GCS...", file=sys.stderr)
            if not gsutil_upload(jsonl_path, gcs_path):
                print(f"  UPLOAD FAILED", file=sys.stderr)
                failed += 1
                continue

            # Load into BigQuery
            print(f"  Loading into BigQuery...", file=sys.stderr)
            if not bq_load(gcs_path):
                print(f"  BQ LOAD FAILED", file=sys.stderr)
                failed += 1
                continue

            # Mark done
            with open(marker, "w") as m:
                m.write(f"{count}\n")

            total_rows += count
            processed += 1
            elapsed = time.time() - start_time
            print(f"  Done ({count:,} rows, {elapsed/60:.1f} min elapsed)", file=sys.stderr)

            # Clean up JSONL to save disk space
            os.unlink(jsonl_path)

        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            failed += 1
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            time.sleep(2)  # Be nice to the API

    elapsed = time.time() - start_time
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"PASYR v3 reload complete in {elapsed/60:.1f} minutes", file=sys.stderr)
    print(f"  Processed: {processed}", file=sys.stderr)
    print(f"  Skipped:   {skipped}", file=sys.stderr)
    print(f"  Failed:    {failed}", file=sys.stderr)
    print(f"  Total rows loaded: {total_rows:,}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
