#!/usr/bin/env python3
"""Backfill the 11 new pfw_* tables for 2001-2020 historical data.

The standard PTFWPRE update pipeline only processes the most-recent ZIP
(2021-2026), so the new tables added in the Part-A expansion only have
recent data.  This script re-parses the 2001-2010 and 2011-2020 ZIPs and
APPENDS to the 11 new tables.  It never touches the 3 original tables
(patent_file_wrapper_v2, pfw_transactions, pfw_continuity) which already
have complete 2001-2026 history.

Architecture (v2 — avoids GCS FUSE stale file handle):
  - Processes ONE YEAR AT A TIME (not the whole ZIP at once)
  - Writes output files to /tmp (NOT GCS FUSE mount) — short open window,
    no multi-hour gzip writes that cause stale file handles
  - After each year: upload to GCS staging, BQ-load, delete local + GCS files
  - Checks source_file presence in BQ before loading — safe to re-run
  - .done markers in /tmp allow within-run fast-skip; BQ checks handle cross-run

Best-practice rules (learned from production failures):
  - Upload files one at a time with gsutil cp  (NOT gsutil -m cp)
  - Load BQ files individually                 (NOT wildcard patterns)
  - Every bq command includes --location=us-west1
  - Every bq load includes --schema_update_option=ALLOW_FIELD_ADDITION
  - Verify row counts after each load
  - Delete /tmp output files after loading to free tmpfs RAM
  - Delete GCS staging files after loading to free bucket space
  - Use .done markers in /tmp to allow safe re-runs (within same container)
  - BQ loads run synchronously (no --nosync) so failures are detected immediately
  - Check source_file in each table before loading to avoid double-loads

Usage (as Cloud Run Job env vars):
  BACKFILL_DECADE   = "2001-2010" | "2011-2020"   (required)
  USPTO_API_KEY     = <key>                         (required for 2011-2020 download)
  GCP_PROJECT_ID    = uspto-data-app
  BIGQUERY_DATASET  = uspto_data
  GCS_BUCKET        = uspto-bulk-staging

The GCS bucket MUST be mounted at /mnt/ptfwpre via Cloud Run volume mount.
  2001-2010 ZIP is at:  /mnt/ptfwpre/ptfwpre/2001-2010-patent-filewrapper-full-json-YYYYMMDD.zip
  2011-2020 ZIP is downloaded to: /mnt/ptfwpre/ptfwpre/2011-2020-patent-filewrapper-full-json-YYYYMMDD.zip
  Parsed output goes to: /tmp/pfw_backfill_{decade}_{year}/ (freed after each year)
  GCS staging: gs://GCS_BUCKET/v2/ptfwpre/backfill_{decade}/
"""

import gzip
import os
import sys
import subprocess
import json
import requests
import zipfile
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
    "applicants":           "pfw_applicants",
    "inventors":            "pfw_inventors",
    "child_continuity":     "pfw_child_continuity",
    "foreign_priority":     "pfw_foreign_priority",
    "publications":         "pfw_publications",
    "pta_summary":          "pfw_patent_term_adjustment",
    "pta_history":          "pfw_pta_history",
    "correspondence":       "pfw_correspondence_address",
    "attorneys":            "pfw_attorneys",
    "doc_metadata":         "pfw_document_metadata",
    "embedded_assignments": "pfw_embedded_assignments",
}

# File prefix for each output key (matches parse_pfw.FILE_PREFIXES)
FILE_PREFIXES = {
    "biblio":               "pfw_biblio",          # NOT loaded — already complete
    "transactions":         "pfw_transactions",    # NOT loaded — already complete
    "continuity":           "pfw_continuity",      # NOT loaded — already complete
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
        return int(lines[-1].strip())
    except Exception:
        return -1


def check_source_file_loaded(table, source_file):
    """Return True if any rows with source_file already exist in the table.

    Uses a COUNT(*) query with LIMIT 1 for speed — we just need to know
    whether ANY rows exist, not how many.  This is the primary idempotency
    guard: if a year was already loaded in a previous run, we skip it.
    """
    result = run_cmd([
        "bq", "query",
        f"--project_id={GCP_PROJECT}",
        f"--location={BQ_LOCATION}",
        "--use_legacy_sql=false",
        "--format=csv",
        f"SELECT COUNT(*) FROM `{BQ_DATASET}.{table}` WHERE source_file = '{source_file}' LIMIT 1",
    ], check=False)
    try:
        lines = result.stdout.strip().split("\n")
        count = int(lines[-1].strip())
        return count > 0
    except Exception:
        return False


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


def delete_from_gcs(gcs_uri):
    """Delete a file from GCS staging after it has been BQ-loaded."""
    run_cmd(["gsutil", "rm", gcs_uri], check=False)


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
    Writes to dest_path on the GCS FUSE mount (persistent across Cloud Run restarts).
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


def process_year(zf, year_filename, zip_stem, decade, done_dir):
    """Parse one year file and append its data to the 11 new BQ tables.

    v2 design — avoids GCS FUSE stale file handle:
      1. Write all 14 output gzip files to /tmp (NOT GCS FUSE mount)
         → Files are open for ~15-20 min max (one year's parse), not hours
      2. Close all 14 writers immediately after parse
      3. For each of the 11 new tables:
           a. Check BQ: if source_file already loaded → skip (idempotent)
           b. Upload /tmp file to GCS staging via gsutil cp (one file at a time)
           c. BQ-load synchronously with WRITE_APPEND
           d. Verify row count increased
           e. Delete /tmp file AND GCS staging file
      4. Delete /tmp files for the 3 skip tables (biblio/transactions/continuity)
      5. Write per-year .done marker to /tmp (for within-run fast-skip)

    Args:
        zf:            Open zipfile.ZipFile (read-only, ZIP on GCS FUSE mount)
        year_filename: Name of year file inside ZIP (e.g. "2001.json")
        zip_stem:      ZIP filename without extension (e.g. "2001-2010-...-20250401")
        decade:        Decade string (e.g. "2001-2010")
        done_dir:      Directory for .done marker files
    """
    from etl.parse_pfw import process_year_file, OUTPUT_KEYS

    year_stem = Path(year_filename).stem   # e.g. "2001"
    done_file = os.path.join(done_dir, f"{decade}_year_{year_stem}.done")

    # Within-run fast-skip: if marker exists from earlier in this same execution
    if os.path.exists(done_file):
        log(f"  Year {year_stem}: done marker exists (within-run), skipping.")
        return

    log(f"\n{'─'*55}")
    log(f"  YEAR {year_stem}  (source: {year_filename})")
    log(f"{'─'*55}")

    # ── Check BQ: which tables already have this year loaded ──────────────────
    log(f"  Checking BQ for existing source_file={year_stem}.json in all 11 tables...")
    loaded_status = {}
    for key, table in NEW_TABLES.items():
        loaded_status[key] = check_source_file_loaded(table, f"{year_stem}.json")
        if loaded_status[key]:
            log(f"    {table}: already has {year_stem}.json ✓ (will skip load)")

    all_loaded = all(loaded_status.values())
    if all_loaded:
        log(f"  Year {year_stem}: ALL 11 tables already loaded — skipping parse.")
        os.makedirs(done_dir, exist_ok=True)
        with open(done_file, "w") as f:
            f.write(f"skipped (all loaded)\n{decade}/{year_stem}\n")
        return

    # ── Step 1: Parse year file → /tmp (NOT GCS mount) ───────────────────────
    tmp_dir = f"/tmp/pfw_backfill_{decade}_{year_stem}"
    os.makedirs(tmp_dir, exist_ok=True)

    # Build output paths: {prefix}_{year_stem}.jsonl.gz  (e.g. pfw_applicants_2001.jsonl.gz)
    tmp_paths = {}
    for key, prefix in FILE_PREFIXES.items():
        tmp_paths[key] = os.path.join(tmp_dir, f"{prefix}_{year_stem}.jsonl.gz")

    log(f"  Parsing {year_filename} → {tmp_dir} ...")

    # Open all 14 writers, process, close immediately
    # Critical: files are closed as soon as the year is done — no multi-hour open handles
    open_files = {}
    year_counts = {}
    try:
        for key in OUTPUT_KEYS:
            open_files[key] = gzip.open(tmp_paths[key], "wt", encoding="utf-8")
        year_counts = process_year_file(zf, year_filename, open_files, min_year=1900)
    finally:
        for key, fh in open_files.items():
            try:
                fh.close()
            except Exception as e:
                log(f"    WARNING: error closing {key} writer: {e}")

    log(f"  Parse complete. Row counts: { {k: v for k, v in year_counts.items() if v > 0} }")

    # ── Step 2: Upload → BQ-load → verify → cleanup for each new table ───────
    gcs_dir = f"v2/ptfwpre/backfill_{decade}"

    for key, table in NEW_TABLES.items():
        prefix    = FILE_PREFIXES[key]
        fname     = f"{prefix}_{year_stem}.jsonl.gz"
        tmp_path  = tmp_paths[key]
        gcs_uri   = f"gs://{GCS_BUCKET}/{gcs_dir}/{fname}"
        row_count = year_counts.get(key, 0)

        if not os.path.exists(tmp_path):
            log(f"    {table}/{year_stem}: output file not found — skipping")
            continue

        # Already loaded in a previous run → skip, delete local file
        if loaded_status[key]:
            log(f"    {table}/{year_stem}: already in BQ — skipping load")
            os.unlink(tmp_path)
            continue

        if row_count == 0:
            log(f"    {table}/{year_stem}: 0 rows parsed — skipping load")
            os.unlink(tmp_path)
            continue

        log(f"\n    Loading {table}/{year_stem} ({row_count:,} rows)...")

        # Row count before load
        before = get_bq_row_count(table)
        log(f"      Before: {before:,} rows")

        # Upload to GCS staging — one file at a time (no -m)
        upload_to_gcs(tmp_path, gcs_uri)

        # BQ load — synchronous, no --nosync (failures detected immediately)
        success = bq_load_append(gcs_uri, table)
        if not success:
            raise RuntimeError(f"BQ load FAILED for {table} from {gcs_uri}")

        # Verify row count increased
        after = get_bq_row_count(table)
        added = after - before
        log(f"      After:  {after:,} rows (+{added:,})")
        if added <= 0:
            log(f"      WARNING: row count did not increase for {table}/{year_stem} — check BQ logs")

        # Clean up: delete /tmp file and GCS staging file
        os.unlink(tmp_path)
        log(f"      Deleted local: {tmp_path}")
        delete_from_gcs(gcs_uri)
        log(f"      Deleted GCS:   {gcs_uri}")
        log(f"      {table}/{year_stem}: ✓ done")

    # ── Step 3: Delete /tmp files for the 3 skip tables ──────────────────────
    for skip_key in ["biblio", "transactions", "continuity"]:
        tmp_path = tmp_paths.get(skip_key)
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Clean up the temp dir if empty
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass  # Not empty or gone — fine

    # ── Step 4: Write per-year done marker ───────────────────────────────────
    os.makedirs(done_dir, exist_ok=True)
    with open(done_file, "w") as f:
        f.write(f"completed\n{decade}/{year_stem}\n")
    log(f"\n  Year {year_stem}: ✓ done marker written.")


def process_zip(zip_path, done_dir):
    """Parse zip_path and append to all 11 new BQ tables, one year at a time.

    Opens the ZIP once (read-only from GCS FUSE mount — just reading compressed data).
    For each year file inside the ZIP, calls process_year() which:
      - Writes output to /tmp (not GCS mount)
      - Uploads to GCS → BQ-loads → verifies → cleans up
    """
    zip_stem = Path(zip_path).stem
    decade   = zip_stem[:9]   # e.g. "2001-2010"

    log(f"\n{'='*60}")
    log(f"BACKFILL ZIP: {zip_stem}")
    log(f"{'='*60}")

    # Log initial row counts for all 11 new tables
    log("\nInitial row counts:")
    for key, table in NEW_TABLES.items():
        count = get_bq_row_count(table)
        log(f"  {table}: {count:,}")

    # Open ZIP read-only (reading from GCS FUSE mount is fine — no long writes)
    with zipfile.ZipFile(zip_path, "r") as zf:
        year_files = sorted(
            [n for n in zf.namelist() if n.endswith(".json")]
        )
        log(f"\nZIP contains {len(year_files)} year files: {year_files}")

        for year_filename in year_files:
            process_year(zf, year_filename, zip_stem, decade, done_dir)

    # Log final row counts
    log(f"\n{'='*60}")
    log(f"BACKFILL COMPLETE: {zip_stem}")
    log(f"{'='*60}")
    log("\nFinal row counts:")
    for key, table in NEW_TABLES.items():
        count = get_bq_row_count(table)
        log(f"  {table}: {count:,}")


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

    log(f"Backfill v2 starting for decade: {decade}")
    log(f"GCS mount: {GCS_MOUNT} (read-only for ZIP; output goes to /tmp, not mount)")
    log(f"BQ dataset: {GCP_PROJECT}.{BQ_DATASET} (location={BQ_LOCATION})")
    log(f"New tables to populate: {list(NEW_TABLES.values())}")

    # Find or download the ZIP
    zip_path = find_or_download_zip(decade, api_key)

    # Parse and load, one year at a time
    process_zip(zip_path, done_dir)

    log("\nBackfill complete.")


if __name__ == "__main__":
    main()
