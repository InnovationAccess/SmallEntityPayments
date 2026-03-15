#!/usr/bin/env python3
"""Backfill the 11 new pfw_* tables for 2001-2020 historical data.

The standard PTFWPRE update pipeline only processes the most-recent ZIP
(2021-2026), so the new tables added in the Part-A expansion only have
recent data.  This script re-parses the 2001-2010 and 2011-2020 ZIPs and
APPENDS to the 11 new tables.  It never touches the 3 original tables
(patent_file_wrapper_v2, pfw_transactions, pfw_continuity) which already
have complete 2001-2026 history.

Best-practice rules (learned from production failures):
  - Upload files one at a time with gsutil cp  (NOT gsutil -m cp)
  - Load BQ files individually                 (NOT wildcard patterns)
  - Every bq command includes --location=us-west1
  - Every bq load includes --schema_update_option=ALLOW_FIELD_ADDITION
  - Verify row counts after each load
  - Delete output files after loading to free GCS FUSE space
  - Use .done markers in /tmp to allow safe re-runs
  - BQ loads run synchronously (no --nosync) so failures are detected immediately

Usage (as Cloud Run Job env vars):
  BACKFILL_DECADE   = "2001-2010" | "2011-2020"   (required)
  USPTO_API_KEY     = <key>                         (required for 2011-2020 download)
  GCP_PROJECT_ID    = uspto-data-app
  BIGQUERY_DATASET  = uspto_data
  GCS_BUCKET        = uspto-bulk-staging

The GCS bucket MUST be mounted at /mnt/ptfwpre via Cloud Run volume mount.
  2001-2010 ZIP is at:  /mnt/ptfwpre/ptfwpre/2001-2010-patent-filewrapper-full-json-YYYYMMDD.zip
  2011-2020 ZIP is downloaded to: /mnt/ptfwpre/ptfwpre/2011-2020-patent-filewrapper-full-json-YYYYMMDD.zip
  Parsed output goes to: /mnt/ptfwpre/v2/ptfwpre/backfill_XXXX-XXXX/
"""

import os
import sys
import subprocess
import json
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Configuration ──────────────────────────────────────────────────────────────

GCS_MOUNT   = "/mnt/ptfwpre"
GCS_BUCKET  = os.environ.get("GCS_BUCKET",        "uspto-bulk-staging")
GCP_PROJECT = os.environ.get("GCP_PROJECT_ID",    "uspto-data-app")
BQ_DATASET  = os.environ.get("BIGQUERY_DATASET",  "uspto_data")
BQ_LOCATION = "us-west1"

# The 11 new tables: output key → BigQuery table name
NEW_TABLES = {
    "applicants":          "pfw_applicants",
    "inventors":           "pfw_inventors",
    "child_continuity":    "pfw_child_continuity",
    "foreign_priority":    "pfw_foreign_priority",
    "publications":        "pfw_publications",
    "pta_summary":         "pfw_patent_term_adjustment",
    "pta_history":         "pfw_pta_history",
    "correspondence":      "pfw_correspondence_address",
    "attorneys":           "pfw_attorneys",
    "doc_metadata":        "pfw_document_metadata",
    "embedded_assignments":"pfw_embedded_assignments",
}

# File prefix for each output key (matches parse_pfw.FILE_PREFIXES)
FILE_PREFIXES = {
    "biblio":               "pfw_biblio",         # NOT loaded — already complete
    "transactions":         "pfw_transactions",   # NOT loaded — already complete
    "continuity":           "pfw_continuity",     # NOT loaded — already complete
    "applicants":           "pfw_applicants",
    "inventors":            "pfw_inventors",
    "child_continuity":     "pfw_child_cont",
    "foreign_priority":     "pfw_foreign_priority",
    "publications":         "pfw_publications",
    "pta_summary":          "pfw_pta_summary",
    "pta_history":          "pfw_pta_history",
    "correspondence":       "pfw_correspondence",
    "attorneys":            "pfw_attorneys",
    "doc_metadata":         "pfw_doc_metadata",
    "embedded_assignments": "pfw_embedded_assign",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def log(msg):
    print(msg, file=sys.stderr, flush=True)


def run_cmd(args, check=True):
    log(f"  $ {' '.join(str(a) for a in args)}")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.stdout.strip():
        log(result.stdout.strip()[:800])
    if result.stderr.strip():
        log(result.stderr.strip()[:800])
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed (exit {result.returncode}): {' '.join(str(a) for a in args)}")
    return result


def get_bq_row_count(table):
    """Return current row count for a BQ table."""
    result = run_cmd([
        "bq", "query",
        f"--project_id={GCP_PROJECT}",
        f"--location={BQ_LOCATION}",
        "--use_legacy_sql=false",
        "--format=csv",
        f"SELECT COUNT(*) FROM `{BQ_DATASET}.{table}`",
    ], check=False)
    try:
        lines = result.stdout.strip().split("\n")
        # CSV output: header row + value row
        return int(lines[-1].strip())
    except Exception:
        return -1


def bq_load_append(gcs_uri, table):
    """Load a single JSONL.GZ file, appending to an existing BQ table.

    Runs synchronously (no --nosync) so failures are caught immediately.
    Uses --schema_update_option=ALLOW_FIELD_ADDITION as a safeguard.
    Does NOT use --replace, so default WRITE_APPEND behavior applies.
    """
    full_table = f"{GCP_PROJECT}:{BQ_DATASET}.{table}"
    result = run_cmd([
        "bq", "load",
        f"--project_id={GCP_PROJECT}",
        f"--location={BQ_LOCATION}",
        "--source_format=NEWLINE_DELIMITED_JSON",
        "--schema_update_option=ALLOW_FIELD_ADDITION",
        full_table,
        gcs_uri,
    ], check=False)
    return result.returncode == 0


def upload_to_gcs(local_path, gcs_uri):
    """Upload a single file to GCS. Uses gsutil cp (NOT -m) per best practices."""
    run_cmd(["gsutil", "cp", local_path, gcs_uri])


# ── USPTO API ─────────────────────────────────────────────────────────────────

def get_ptfwpre_file_list(api_key):
    """Return list of PTFWPRE data files from USPTO API."""
    url = "https://api.uspto.gov/api/v1/datasets/products/PTFWPRE"
    resp = requests.get(url, headers={"X-API-KEY": api_key}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    files = data["bulkDataProductBag"][0]["productFileBag"]["fileDataBag"]
    return [f for f in files if f["fileName"].endswith(".zip") and f["fileTypeText"] == "Data"]


def download_zip(api_key, file_info, dest_path):
    """Stream-download a USPTO ZIP to dest_path.

    Uses 8 MB chunks to avoid loading the full file into RAM.
    Writes directly to dest_path on the GCS FUSE mount.
    """
    url = file_info["fileDownloadURI"]
    size_gb = file_info.get("fileSize", 0) / 1024 ** 3
    filename = file_info["fileName"]
    log(f"  Downloading {filename} ({size_gb:.1f} GB) → {dest_path}")
    log("  This may take 1-2 hours for a 28 GB file.")

    headers = {"X-API-KEY": api_key}
    downloaded = 0
    with requests.get(url, headers=headers, stream=True, timeout=7200) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as out:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                out.write(chunk)
                downloaded += len(chunk)
                if downloaded % (512 * 1024 * 1024) == 0:
                    log(f"    ... {downloaded / 1024**3:.1f} GB downloaded")
    log(f"  Download complete: {dest_path} ({downloaded / 1024**3:.1f} GB)")


# ── Core backfill logic ────────────────────────────────────────────────────────

def find_or_download_zip(decade, api_key):
    """Locate the ZIP for the given decade on the GCS mount, downloading if needed.

    Looks for any file matching the decade prefix (e.g. 2011-2020-*) in
    /mnt/ptfwpre/ptfwpre/.  Downloads from USPTO if not found.
    """
    zip_dir = os.path.join(GCS_MOUNT, "ptfwpre")

    # Check if ZIP already exists
    if os.path.isdir(zip_dir):
        for fname in sorted(os.listdir(zip_dir)):
            if fname.startswith(decade) and fname.endswith(".zip"):
                zip_path = os.path.join(zip_dir, fname)
                log(f"Found existing ZIP: {zip_path}")
                return zip_path

    # Need to download — requires API key
    if not api_key:
        raise RuntimeError(
            f"No ZIP found for {decade} in {zip_dir} and USPTO_API_KEY not set — cannot download."
        )

    log(f"ZIP for {decade} not found in GCS mount. Fetching from USPTO API...")
    file_list = get_ptfwpre_file_list(api_key)
    match = next((f for f in file_list if f["fileName"].startswith(decade)), None)
    if not match:
        available = [f["fileName"] for f in file_list]
        raise RuntimeError(f"No PTFWPRE file found for decade {decade}. Available: {available}")

    os.makedirs(zip_dir, exist_ok=True)
    dest_path = os.path.join(zip_dir, match["fileName"])
    download_zip(api_key, match, dest_path)
    return dest_path


def process_zip(zip_path, done_dir):
    """Parse zip_path and append to all 11 new BQ tables.

    Steps:
      1. Parse ZIP → 14 JSONL files in /mnt/ptfwpre/v2/ptfwpre/backfill_DECADE/
      2. For each of the 11 new tables:
           a. The file is already on GCS (written via FUSE mount)
           b. BQ-load synchronously with WRITE_APPEND
           c. Verify row count increased
           d. Delete the local (GCS-mount) file to free space
      3. Delete the 3 skip-tables' files (biblio/transactions/continuity)
      4. Write .done marker to /tmp
    """
    from etl.parse_pfw import parse_zip, FILE_PREFIXES as PFW_PREFIXES

    zip_stem  = Path(zip_path).stem
    decade    = zip_stem[:9]          # e.g. "2001-2010"
    done_file = os.path.join(done_dir, f"{zip_stem}.done")

    if os.path.exists(done_file):
        log(f"Already processed (marker exists): {zip_stem}")
        return

    log(f"\n{'='*60}")
    log(f"PROCESSING: {zip_stem}")
    log(f"{'='*60}")

    # Output goes to the GCS mount so files land directly in GCS
    output_dir = os.path.join(GCS_MOUNT, "v2", "ptfwpre", f"backfill_{decade}")
    os.makedirs(output_dir, exist_ok=True)

    # ── Step 1: Parse ──────────────────────────────────────────────
    log(f"Parsing {zip_path} → {output_dir}")
    counts = parse_zip(zip_path, output_dir, min_year=2001)
    log(f"Parse complete. Row counts: {counts}")

    # ── Step 2: BQ-append each new table, one at a time ───────────
    for key, table in NEW_TABLES.items():
        prefix       = FILE_PREFIXES[key]
        jsonl_name   = f"{prefix}_{zip_stem}.jsonl.gz"
        local_path   = os.path.join(output_dir, jsonl_name)
        gcs_uri      = f"gs://{GCS_BUCKET}/v2/ptfwpre/backfill_{decade}/{jsonl_name}"
        row_count    = counts.get(key, 0)

        if not os.path.exists(local_path):
            log(f"  WARNING: {jsonl_name} not found — skipping {table}")
            continue

        if row_count == 0:
            log(f"  {table}: 0 rows parsed — skipping load")
            os.unlink(local_path)
            continue

        log(f"\n  Loading {table} ({row_count:,} rows)...")

        # Row count before load
        before = get_bq_row_count(table)
        log(f"    {table} rows before load: {before:,}")

        # BQ load (file already in GCS via mount — use gs:// URI)
        success = bq_load_append(gcs_uri, table)
        if not success:
            raise RuntimeError(f"BQ load FAILED for {table} from {gcs_uri}")

        # Verify row count increased
        after = get_bq_row_count(table)
        added = after - before
        log(f"    {table} rows after load: {after:,} (+{added:,})")
        if added <= 0:
            log(f"    WARNING: Row count did not increase for {table} — load may have failed silently")

        # Delete the local file to free GCS space
        os.unlink(local_path)
        log(f"    {table}: loaded and deleted from staging.")

    # ── Step 3: Delete the 3 skip-tables' files ───────────────────
    for skip_key in ["biblio", "transactions", "continuity"]:
        prefix     = FILE_PREFIXES[skip_key]
        jsonl_name = f"{prefix}_{zip_stem}.jsonl.gz"
        local_path = os.path.join(output_dir, jsonl_name)
        if os.path.exists(local_path):
            os.unlink(local_path)
            log(f"  Deleted (skip): {jsonl_name}")

    # ── Step 4: Write done marker ──────────────────────────────────
    os.makedirs(done_dir, exist_ok=True)
    with open(done_file, "w") as f:
        f.write(f"completed\n{zip_stem}\n")
    log(f"\nDone marker written: {done_file}")
    log(f"{'='*60}")
    log(f"COMPLETED: {zip_stem}")
    log(f"{'='*60}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    decade   = os.environ.get("BACKFILL_DECADE", "").strip()
    api_key  = os.environ.get("USPTO_API_KEY",   "").strip()
    done_dir = "/tmp/backfill_pfw_done"

    if not decade:
        log("Error: BACKFILL_DECADE env var not set. Use '2001-2010' or '2011-2020'.")
        sys.exit(1)

    if not os.path.exists(GCS_MOUNT):
        log(f"Error: GCS mount not found at {GCS_MOUNT}.")
        log("This script requires the Cloud Run GCS volume mount to be configured.")
        sys.exit(1)

    log(f"Backfill starting for decade: {decade}")
    log(f"GCS mount: {GCS_MOUNT}")
    log(f"BQ dataset: {GCP_PROJECT}.{BQ_DATASET} (location={BQ_LOCATION})")
    log(f"New tables to populate: {list(NEW_TABLES.values())}")

    # Find or download the ZIP
    zip_path = find_or_download_zip(decade, api_key)

    # Parse and load
    process_zip(zip_path, done_dir)

    log("Backfill complete.")


if __name__ == "__main__":
    main()
