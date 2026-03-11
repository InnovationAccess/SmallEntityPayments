#!/usr/bin/env python3
"""Fix invalid dates in PASYR JSONL.gz files.

Reads each JSONL.gz file, replaces date fields with years < 1700 or > 2100
with null, and writes the cleaned output. Fixes the '0000-01-01' problem
that causes BigQuery load failures.

Usage:
    python fix_bad_dates.py <input_dir> <pattern>

Example:
    python fix_bad_dates.py /home/uzi/ptfwpre-staging/pasyr 'pasyr_ad19880101-*.jsonl.gz'
"""

import glob
import gzip
import json
import os
import sys
import tempfile


DATE_FIELDS = {
    "recorded_date",
    "last_update_date",
    "assignor_execution_date",
    "filing_date",
    "grant_date",
    "event_date",
    "parent_filing_date",
    "earliest_publication_date",
    "effective_filing_date",
    "citing_grant_date",
}


def is_valid_date(val: str) -> bool:
    """Check if a date string has a valid year (1700-2100)."""
    if not val or not isinstance(val, str) or len(val) < 4:
        return True  # null/empty is fine
    try:
        year = int(val[:4])
        return 1700 <= year <= 2100
    except (ValueError, TypeError):
        return True  # non-date string, skip


def fix_file(filepath: str) -> tuple[int, int]:
    """Fix bad dates in a single JSONL.gz file. Returns (total_rows, fixed_rows)."""
    total = 0
    fixed = 0

    # Write to temp file, then replace
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jsonl.gz", dir=os.path.dirname(filepath))
    os.close(tmp_fd)

    try:
        with gzip.open(filepath, "rt", encoding="utf-8") as fin, \
             gzip.open(tmp_path, "wt", encoding="utf-8") as fout:
            for line in fin:
                total += 1
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                row_fixed = False
                for field in DATE_FIELDS:
                    if field in row and row[field] and not is_valid_date(row[field]):
                        row[field] = None
                        row_fixed = True
                if row_fixed:
                    fixed += 1
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")

        # Replace original with fixed version
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return total, fixed


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input_dir> <glob_pattern>")
        sys.exit(1)

    input_dir = sys.argv[1]
    pattern = sys.argv[2]

    files = sorted(glob.glob(os.path.join(input_dir, pattern)))
    if not files:
        print(f"No files match {pattern} in {input_dir}")
        sys.exit(1)

    print(f"Processing {len(files)} files...", file=sys.stderr)

    grand_total = 0
    grand_fixed = 0

    for i, filepath in enumerate(files):
        fname = os.path.basename(filepath)
        total, fixed = fix_file(filepath)
        grand_total += total
        grand_fixed += fixed
        print(f"  [{i+1}/{len(files)}] {fname}: {total:,} rows, {fixed} fixed",
              file=sys.stderr)

    print(f"\nDone: {grand_total:,} total rows, {grand_fixed:,} dates fixed",
          file=sys.stderr)


if __name__ == "__main__":
    main()
