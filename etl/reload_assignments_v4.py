#!/usr/bin/env python3
"""Reload normalized assignment tables from the PASYR annual backfile.

Downloads the most recent PASYR backfile from the USPTO ODP API,
parses each shard with the v4 normalized parser, uploads 4 JSONL.gz
files per shard to GCS, and loads into BigQuery.

Usage:
    python reload_assignments_v4.py [--min-year 2006] [--dry-run]

Environment:
    USPTO_API_KEY     - Required for ODP API authentication
    GCP_PROJECT_ID    - BigQuery project (default: uspto-data-app)
    BIGQUERY_DATASET  - BigQuery dataset (default: uspto_data)
    GCS_BUCKET        - GCS bucket for staging (default: uspto-bulk-staging)
"""

import glob
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl.parse_assignments_xml_v4 import parse_input

API_BASE = "https://api.uspto.gov"
PRODUCT_ID = "PASYR"

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "uspto-data-app")
BQ_DATASET = os.environ.get("BIGQUERY_DATASET", "uspto_data")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "uspto-bulk-staging")
BQ_LOCATION = "us-west1"
GCS_DIR = "v4/pasyr"

# Mapping from parser output file prefixes to BQ table names
TABLE_MAP = {
    "records": "pat_assign_records",
    "assignors": "pat_assign_assignors",
    "assignees": "pat_assign_assignees",
    "documents": "pat_assign_documents",
}


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


def bq_load(gcs_path: str, table: str) -> bool:
    """Load a JSONL.gz file into BigQuery."""
    full_table = f"{GCP_PROJECT}:{BQ_DATASET}.{table}"
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
    """Select only the most recent backfile release."""
    prefixes = {}
    for f in files:
        name = f["fileName"]
        parts = name.rsplit("-", 1)
        if len(parts) == 2:
            prefix = parts[0]
            prefixes.setdefault(prefix, []).append(f)

    if not prefixes:
        return files

    latest_prefix = max(prefixes.keys())
    selected = prefixes[latest_prefix]
    selected.sort(key=lambda f: f["fileName"])

    print(f"Selected backfile release: {latest_prefix} ({len(selected)} shards)", file=sys.stderr)
    other_count = len(files) - len(selected)
    if other_count > 0:
        print(f"Skipping {other_count} files from older releases", file=sys.stderr)

    return selected


def main():
    min_year = 2006
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
    done_dir = "/tmp/reload-v4/.done"
    os.makedirs(done_dir, exist_ok=True)

    total_counts = {"records": 0, "assignors": 0, "assignees": 0, "documents": 0}
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

            # Parse with v4 parser (outputs 4 files)
            parse_dir = f"/tmp/reload-v4/pasyr_{filename}"
            os.makedirs(parse_dir, exist_ok=True)
            counts = parse_input(tmp_path, parse_dir, min_year)
            print(f"  Parsed {counts['records']:,} records, "
                  f"{counts['assignors']:,} assignors, "
                  f"{counts['assignees']:,} assignees, "
                  f"{counts['documents']:,} documents", file=sys.stderr)

            if counts["records"] == 0:
                print(f"  No records — skipping upload", file=sys.stderr)
                with open(marker, "w") as m:
                    m.write("0\n")
                continue

            # Upload and load each of the 4 output files
            all_ok = True
            for prefix, table in TABLE_MAP.items():
                pattern = os.path.join(parse_dir, f"{prefix}_*.jsonl.gz")
                matches = glob.glob(pattern)
                for jsonl_path in matches:
                    gcs_path = f"gs://{GCS_BUCKET}/{GCS_DIR}/{os.path.basename(jsonl_path)}"
                    print(f"  Uploading {prefix} to GCS...", file=sys.stderr)
                    if not gsutil_upload(jsonl_path, gcs_path):
                        all_ok = False
                        break
                    print(f"  Loading {prefix} into BigQuery...", file=sys.stderr)
                    if not bq_load(gcs_path, table):
                        all_ok = False
                        break
                if not all_ok:
                    break

            if not all_ok:
                print(f"  LOAD FAILED", file=sys.stderr)
                failed += 1
                continue

            # Mark done
            with open(marker, "w") as m:
                m.write(f"{sum(counts.values())}\n")

            for key in total_counts:
                total_counts[key] += counts[key]
            processed += 1
            elapsed = time.time() - start_time
            print(f"  Done ({counts['records']:,} records, {elapsed/60:.1f} min elapsed)",
                  file=sys.stderr)

            # Clean up parsed files to save disk space
            for jsonl_path in glob.glob(os.path.join(parse_dir, "*.jsonl.gz")):
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
    print(f"PASYR v4 reload complete in {elapsed/60:.1f} minutes", file=sys.stderr)
    print(f"  Processed: {processed}", file=sys.stderr)
    print(f"  Skipped:   {skipped}", file=sys.stderr)
    print(f"  Failed:    {failed}", file=sys.stderr)
    print(f"  Records:   {total_counts['records']:,}", file=sys.stderr)
    print(f"  Assignors: {total_counts['assignors']:,}", file=sys.stderr)
    print(f"  Assignees: {total_counts['assignees']:,}", file=sys.stderr)
    print(f"  Documents: {total_counts['documents']:,}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
