#!/usr/bin/env python3
"""Automated update pipeline for USPTO data.

Downloads, parses, uploads to GCS, and loads into BigQuery for each data source.
Designed to run as a Cloud Run Job triggered by Cloud Scheduler.

Usage:
    python update_pipeline.py <source>

Sources:
    ptblxml   - Weekly patent grant citations (forward_citations table)
    pasdl     - Daily patent assignment updates (patent_assignments_v3 table)
    ptmnfee2  - Maintenance fee events (maintenance_fee_events_v2 table)
    ptfwpre   - Patent file wrapper (patent_file_wrapper_v2, pfw_transactions, pfw_continuity)
    entity    - Rebuild entity_names from current data (no download needed)

Environment:
    USPTO_API_KEY     - Required for ptblxml, pasdl, ptmnfee2, ptfwpre
    GCP_PROJECT_ID    - BigQuery project (default: uspto-data-app)
    BIGQUERY_DATASET  - BigQuery dataset (default: uspto_data)
    GCS_BUCKET        - GCS bucket for staging (default: uspto-bulk-staging)
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure the project root is on sys.path so etl.* and utils.* imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Project defaults
GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "uspto-data-app")
BQ_DATASET = os.environ.get("BIGQUERY_DATASET", "uspto_data")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "uspto-bulk-staging")
BQ_LOCATION = "us-west1"

# How many recent files to process per source (limits runtime for scheduled runs)
RECENT_LIMITS = {
    "ptblxml": 4,     # ~4 weekly files = 1 month of catch-up
    "pasdl": 30,      # ~30 daily files = 1 month of catch-up
    "ptmnfee2": 1,    # single file (full replacement)
    "ptfwpre": 1,     # single file (full replacement, very large)
}


def run_cmd(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"  CMD: {' '.join(cmd[:6])}...", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[:500]}", file=sys.stderr)
    return result


def gsutil_upload(local_path: str, gcs_path: str) -> bool:
    """Upload a single file to GCS. Returns True on success."""
    result = run_cmd(["gsutil", "cp", local_path, gcs_path])
    return result.returncode == 0


def bq_load(gcs_path: str, table: str) -> bool:
    """Submit async BigQuery load job. Returns True on success."""
    full_table = f"{GCP_PROJECT}:{BQ_DATASET}.{table}"
    result = run_cmd([
        "bq", "load", "--nosync",
        f"--project_id={GCP_PROJECT}",
        f"--location={BQ_LOCATION}",
        "--source_format=NEWLINE_DELIMITED_JSON",
        full_table, gcs_path,
    ])
    return result.returncode == 0


def bq_query(sql: str, timeout: int = 600) -> str:
    """Run a BigQuery SQL query."""
    result = run_cmd([
        "bq", "query",
        f"--project_id={GCP_PROJECT}",
        f"--location={BQ_LOCATION}",
        "--nouse_legacy_sql",
        sql,
    ], timeout=timeout)
    return result.stdout


def write_etl_log(run_id, source, status, started_at, completed_at=None,
                   files_processed=0, files_skipped=0, files_failed=0,
                   rows_loaded=0, duration_seconds=0, details=None,
                   error_message=None):
    """Write a log entry to the etl_log BigQuery table."""
    started_str = started_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    completed_str = completed_at.strftime("%Y-%m-%d %H:%M:%S UTC") if completed_at else started_str
    details_escaped = (details or "").replace("'", "\\'")
    error_escaped = (error_message or "").replace("'", "\\'")

    sql = f"""
    INSERT INTO `{GCP_PROJECT}.{BQ_DATASET}.etl_log`
    (run_id, source, status, started_at, completed_at,
     files_processed, files_skipped, files_failed, rows_loaded,
     duration_seconds, details, error_message)
    VALUES (
      '{run_id}', '{source}', '{status}',
      TIMESTAMP('{started_str}'), TIMESTAMP('{completed_str}'),
      {files_processed}, {files_skipped}, {files_failed}, {rows_loaded},
      {round(duration_seconds, 1)},
      {f"'{details_escaped}'" if details else "NULL"},
      {f"'{error_escaped}'" if error_message else "NULL"}
    )
    """
    try:
        bq_query(sql)
        print(f"  ETL log written: {source} -> {status}", file=sys.stderr)
    except Exception as e:
        print(f"  WARNING: Failed to write ETL log: {e}", file=sys.stderr)


def upload_and_load(local_path: str, gcs_dir: str, table: str) -> bool:
    """Upload a JSONL.gz file to GCS and submit BQ load."""
    filename = os.path.basename(local_path)
    gcs_path = f"gs://{GCS_BUCKET}/{gcs_dir}/{filename}"

    print(f"  Uploading {filename} to GCS...", file=sys.stderr)
    if not gsutil_upload(local_path, gcs_path):
        print(f"  UPLOAD FAILED: {filename}", file=sys.stderr)
        return False

    print(f"  Loading into {table}...", file=sys.stderr)
    if not bq_load(gcs_path, table):
        print(f"  BQ LOAD FAILED: {filename}", file=sys.stderr)
        return False

    time.sleep(3)
    return True


# ─── PTBLXML: Weekly Forward Citations ───────────────────────────

def update_ptblxml(work_dir: str) -> dict:
    """Download new PTBLXML weekly files and load citations."""
    from etl.download_ptblxml import get_api_key, list_files, download_file
    from etl.parse_ptblxml import parse_zip

    stats = {"processed": 0, "skipped": 0, "failed": 0, "rows": 0}

    api_key = get_api_key()
    files = list_files(api_key)
    files.sort(key=lambda f: f["fileName"])

    done_dir = os.path.join(work_dir, ".done")
    os.makedirs(done_dir, exist_ok=True)

    all_new = [f for f in files if not os.path.exists(os.path.join(done_dir, f["fileName"] + ".done"))]
    stats["skipped"] = len(files) - len(all_new)

    if not all_new:
        print("PTBLXML: No new files to process.", file=sys.stderr)
        return stats

    limit = RECENT_LIMITS["ptblxml"]
    new_files = all_new[-limit:] if len(all_new) > limit else all_new
    stats["skipped"] += len(all_new) - len(new_files)

    print(f"PTBLXML: Processing {len(new_files)} new files", file=sys.stderr)

    for i, f in enumerate(new_files):
        filename = f["fileName"]
        print(f"\n  [{i+1}/{len(new_files)}] {filename}...", file=sys.stderr)

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if not download_file(api_key, f["fileDownloadURI"], tmp_path):
                stats["failed"] += 1
                continue

            jsonl_path = os.path.join(work_dir, f"citations_{filename}.jsonl.gz")
            count, _ = parse_zip(tmp_path, jsonl_path)
            print(f"  Parsed {count:,} citation rows", file=sys.stderr)

            if count > 0 and upload_and_load(jsonl_path, "v2/citations", "forward_citations"):
                with open(os.path.join(done_dir, filename + ".done"), "w") as m:
                    m.write(f"{count}\n")
                stats["processed"] += 1
                stats["rows"] += count
                print(f"  Loaded successfully", file=sys.stderr)
            else:
                stats["failed"] += 1
        except Exception as e:
            stats["failed"] += 1
            print(f"  ERROR: {e}", file=sys.stderr)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            time.sleep(2)

    return stats


# ─── PASDL: Daily Assignment Updates ─────────────────────────────

def update_pasdl(work_dir: str) -> dict:
    """Download new PASDL daily files and load assignments."""
    from etl.download_pasdl import get_api_key, list_files, download_file
    from etl.parse_assignments_xml_v3 import parse_input

    stats = {"processed": 0, "skipped": 0, "failed": 0, "rows": 0}

    api_key = get_api_key()
    files = list_files(api_key)
    files.sort(key=lambda f: f["fileName"])

    done_dir = os.path.join(work_dir, ".done")
    os.makedirs(done_dir, exist_ok=True)

    all_new = [f for f in files if not os.path.exists(os.path.join(done_dir, f["fileName"] + ".done"))]
    stats["skipped"] = len(files) - len(all_new)

    if not all_new:
        print("PASDL: No new files to process.", file=sys.stderr)
        return stats

    limit = RECENT_LIMITS["pasdl"]
    new_files = all_new[-limit:] if len(all_new) > limit else all_new
    stats["skipped"] += len(all_new) - len(new_files)

    print(f"PASDL: Processing {len(new_files)} new files", file=sys.stderr)

    for i, f in enumerate(new_files):
        filename = f["fileName"]
        print(f"\n  [{i+1}/{len(new_files)}] {filename}...", file=sys.stderr)

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if not download_file(api_key, f["fileDownloadURI"], tmp_path):
                stats["failed"] += 1
                continue

            jsonl_path = os.path.join(work_dir, f"pasdl_{filename}.jsonl.gz")
            count, _ = parse_input(tmp_path, jsonl_path, min_year=2006)
            print(f"  Parsed {count:,} assignment rows", file=sys.stderr)

            if count > 0 and upload_and_load(jsonl_path, "v3/pasdl", "patent_assignments_v3"):
                with open(os.path.join(done_dir, filename + ".done"), "w") as m:
                    m.write(f"{count}\n")
                stats["processed"] += 1
                stats["rows"] += count
                print(f"  Loaded successfully", file=sys.stderr)
            else:
                stats["failed"] += 1
        except Exception as e:
            stats["failed"] += 1
            print(f"  ERROR: {e}", file=sys.stderr)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            time.sleep(2)

    # Rebuild entity_names after assignment updates
    print("\nRebuilding entity_names after PASDL update...", file=sys.stderr)
    rebuild_entity_names()
    return stats


# ─── PTMNFEE2: Maintenance Fee Events ────────────────────────────

def update_ptmnfee2(work_dir: str) -> dict:
    """Download latest PTMNFEE2 file and replace maintenance_fee_events_v2."""
    import requests
    from etl.parse_maintenance_fees_v2 import parse_zip as parse_maint_zip

    stats = {"processed": 0, "skipped": 0, "failed": 0, "rows": 0}

    api_key = os.environ.get("USPTO_API_KEY")
    if not api_key:
        print("Error: USPTO_API_KEY not set", file=sys.stderr)
        return stats

    url = f"https://api.uspto.gov/api/v1/datasets/products/PTMNFEE2"
    resp = requests.get(url, headers={"X-API-KEY": api_key})
    resp.raise_for_status()
    data = resp.json()
    product = data["bulkDataProductBag"][0]
    files = product["productFileBag"]["fileDataBag"]
    data_files = [f for f in files if f["fileName"].endswith(".zip") and f["fileTypeText"] == "Data"]
    data_files.sort(key=lambda f: f["fileName"])

    if not data_files:
        print("PTMNFEE2: No data files found.", file=sys.stderr)
        return stats

    latest = data_files[-1]
    filename = latest["fileName"]

    done_dir = os.path.join(work_dir, ".done")
    os.makedirs(done_dir, exist_ok=True)
    marker = os.path.join(done_dir, filename + ".done")

    if os.path.exists(marker):
        print(f"PTMNFEE2: {filename} already processed.", file=sys.stderr)
        stats["skipped"] = 1
        return stats

    print(f"PTMNFEE2: Processing {filename} ({latest['fileSize']/1024/1024:.1f} MB)...",
          file=sys.stderr)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        from etl.download_ptblxml import download_file
        if not download_file(api_key, latest["fileDownloadURI"], tmp_path):
            stats["failed"] = 1
            return stats

        jsonl_path = os.path.join(work_dir, f"maint_{filename}.jsonl.gz")
        count = parse_maint_zip(tmp_path, jsonl_path)
        print(f"  Parsed {count:,} maintenance fee rows", file=sys.stderr)

        print("  Truncating maintenance_fee_events_v2...", file=sys.stderr)
        bq_query(f"TRUNCATE TABLE `{BQ_DATASET}.maintenance_fee_events_v2`")

        if count > 0 and upload_and_load(jsonl_path, "v2/ptmnfee2", "maintenance_fee_events_v2"):
            with open(marker, "w") as m:
                m.write(f"{count}\n")
            stats["processed"] = 1
            stats["rows"] = count
            print(f"  Loaded successfully", file=sys.stderr)
        else:
            stats["failed"] = 1
    except Exception as e:
        stats["failed"] = 1
        print(f"  ERROR: {e}", file=sys.stderr)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return stats


# ─── PTFWPRE: Patent File Wrapper ────────────────────────────────

def update_ptfwpre(work_dir: str) -> dict:
    """Download latest PTFWPRE file and reload patent file wrapper tables.

    PTFWPRE files are large (~2-6 GB ZIPs) and contain complete snapshots.
    We process the most recent one and do a full table replacement.
    """
    import requests
    from etl.parse_pfw import parse_zip as parse_pfw_zip

    stats = {"processed": 0, "skipped": 0, "failed": 0, "rows": 0}

    api_key = os.environ.get("USPTO_API_KEY")
    if not api_key:
        print("Error: USPTO_API_KEY not set", file=sys.stderr)
        return stats

    url = f"https://api.uspto.gov/api/v1/datasets/products/PTFWPRE"
    resp = requests.get(url, headers={"X-API-KEY": api_key})
    resp.raise_for_status()
    data = resp.json()
    product = data["bulkDataProductBag"][0]
    files = product["productFileBag"]["fileDataBag"]
    data_files = [f for f in files if f["fileName"].endswith(".zip") and f["fileTypeText"] == "Data"]
    data_files.sort(key=lambda f: f["fileName"])

    if not data_files:
        print("PTFWPRE: No data files found.", file=sys.stderr)
        return stats

    latest = data_files[-1]
    filename = latest["fileName"]

    done_dir = os.path.join(work_dir, ".done")
    os.makedirs(done_dir, exist_ok=True)
    marker = os.path.join(done_dir, filename + ".done")

    if os.path.exists(marker):
        print(f"PTFWPRE: {filename} already processed.", file=sys.stderr)
        stats["skipped"] = 1
        return stats

    print(f"PTFWPRE: Processing {filename} ({latest['fileSize']/1024/1024/1024:.1f} GB)...",
          file=sys.stderr)
    print("  WARNING: This is a large download and may take 1+ hours.", file=sys.stderr)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        from etl.download_ptblxml import download_file
        if not download_file(api_key, latest["fileDownloadURI"], tmp_path):
            stats["failed"] = 1
            return stats

        counts = parse_pfw_zip(tmp_path, work_dir)
        total_rows = sum(counts.values())
        print(f"  Parsed: biblio={counts.get('biblio',0):,}, "
              f"txn={counts.get('transactions',0):,}, "
              f"continuity={counts.get('continuity',0):,}", file=sys.stderr)

        for table in ["patent_file_wrapper_v2", "pfw_transactions", "pfw_continuity"]:
            print(f"  Truncating {table}...", file=sys.stderr)
            bq_query(f"TRUNCATE TABLE `{BQ_DATASET}.{table}`")

        import glob
        for pattern, table in [
            ("pfw_biblio_*.jsonl.gz", "patent_file_wrapper_v2"),
            ("pfw_transactions_*.jsonl.gz", "pfw_transactions"),
            ("pfw_continuity_*.jsonl.gz", "pfw_continuity"),
        ]:
            for fpath in sorted(glob.glob(os.path.join(work_dir, pattern))):
                upload_and_load(fpath, "v2/ptfwpre", table)

        with open(marker, "w") as m:
            m.write(f"done\n")
        stats["processed"] = 1
        stats["rows"] = total_rows
        print(f"  PTFWPRE update complete", file=sys.stderr)

        print("\nRebuilding entity_names after PTFWPRE update...", file=sys.stderr)
        rebuild_entity_names()

    except Exception as e:
        stats["failed"] = 1
        print(f"  ERROR: {e}", file=sys.stderr)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return stats


# ─── Entity Names Rebuild ────────────────────────────────────────

def rebuild_entity_names():
    """Rebuild entity_names table from current data."""
    print("Rebuilding entity_names table...", file=sys.stderr)

    sql = f"""
    CREATE OR REPLACE TABLE `{BQ_DATASET}.entity_names`
    CLUSTER BY entity_name
    AS
    WITH all_names AS (
      SELECT first_applicant_name AS entity_name
      FROM `{BQ_DATASET}.patent_file_wrapper_v2`
      WHERE first_applicant_name IS NOT NULL
      UNION ALL
      SELECT first_inventor_name AS entity_name
      FROM `{BQ_DATASET}.patent_file_wrapper_v2`
      WHERE first_inventor_name IS NOT NULL
        AND first_inventor_name != first_applicant_name
      UNION ALL
      SELECT assignee_name AS entity_name
      FROM `{BQ_DATASET}.patent_assignments_v3`
      WHERE assignee_name IS NOT NULL
      UNION ALL
      SELECT assignor_name AS entity_name
      FROM `{BQ_DATASET}.patent_assignments_v3`
      WHERE assignor_name IS NOT NULL
    )
    SELECT entity_name, COUNT(*) AS frequency
    FROM all_names
    GROUP BY entity_name
    """
    result = bq_query(sql, timeout=600)
    print(f"  entity_names rebuilt.", file=sys.stderr)
    return result


# ─── Main ─────────────────────────────────────────────────────────

SOURCES = {
    "ptblxml": update_ptblxml,
    "pasdl": update_pasdl,
    "ptmnfee2": update_ptmnfee2,
    "ptfwpre": update_ptfwpre,
    "entity": lambda d: rebuild_entity_names() or {"processed": 1, "skipped": 0, "failed": 0, "rows": 0},
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in SOURCES:
        print(f"Usage: {sys.argv[0]} <{'|'.join(SOURCES.keys())}>")
        print(f"\nSources:")
        print(f"  ptblxml  - Weekly patent grant citations")
        print(f"  pasdl    - Daily patent assignment updates")
        print(f"  ptmnfee2 - Maintenance fee events")
        print(f"  ptfwpre  - Patent file wrapper (3 tables)")
        print(f"  entity   - Rebuild entity_names table")
        sys.exit(1)

    source = sys.argv[1]
    work_dir = os.environ.get("WORK_DIR", f"/tmp/update-{source}")
    os.makedirs(work_dir, exist_ok=True)

    run_id = str(uuid.uuid4())[:8]
    started_at = datetime.now(timezone.utc)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"USPTO Update Pipeline: {source.upper()} (run {run_id})", file=sys.stderr)
    print(f"Work directory: {work_dir}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    start = time.time()
    stats = None
    error_msg = None

    try:
        stats = SOURCES[source](work_dir)
    except Exception as e:
        error_msg = str(e)
        print(f"\nFATAL ERROR: {e}", file=sys.stderr)

    elapsed = time.time() - start
    completed_at = datetime.now(timezone.utc)

    # Determine status
    if stats is None:
        stats = {"processed": 0, "skipped": 0, "failed": 0, "rows": 0}

    if error_msg:
        status = "failed"
    elif stats.get("failed", 0) > 0 and stats.get("processed", 0) == 0:
        status = "failed"
    elif stats.get("processed", 0) == 0 and stats.get("rows", 0) == 0:
        status = "no_updates"
    else:
        status = "success"

    # Write ETL log entry
    write_etl_log(
        run_id=run_id,
        source=source,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        files_processed=stats.get("processed", 0),
        files_skipped=stats.get("skipped", 0),
        files_failed=stats.get("failed", 0),
        rows_loaded=stats.get("rows", 0),
        duration_seconds=elapsed,
        error_message=error_msg,
    )

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Pipeline complete in {elapsed/60:.1f} minutes — {status}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if error_msg:
        sys.exit(1)


if __name__ == "__main__":
    main()
