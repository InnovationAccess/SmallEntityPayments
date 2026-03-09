#!/bin/bash
# Download and parse all PASYR files one at a time, loading each into BigQuery.
# Handles disk constraints by processing and loading one file at a time.

set -e

API_KEY="${USPTO_API_KEY:?Set USPTO_API_KEY environment variable}"
MIN_YEAR=2006
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARSER="$SCRIPT_DIR/parse_assignments_xml.py"
PROJECT="uspto-data-app"
TABLE="uspto_data.patent_assignments"
TEMP_JSONL="/tmp/assignments_batch.jsonl.gz"

# Get file list from API
echo "Fetching PASYR file listing..."
FILE_LIST=$(curl -s -H "x-api-key: $API_KEY" \
  "https://api.uspto.gov/api/v1/datasets/products/PASYR" | \
  python3 -c "
import json, sys
data = json.load(sys.stdin)
files = data.get('bulkDataProductBag', [{}])[0].get('productFileBag', {}).get('fileDataBag', [])
for f in files:
    print(f['fileName'])
")

TOTAL=$(echo "$FILE_LIST" | wc -l)
echo "Found $TOTAL files to process"

GRAND_TOTAL=0
COUNT=0
for FNAME in $FILE_LIST; do
    COUNT=$((COUNT + 1))
    echo ""
    echo "=== [$COUNT/$TOTAL] $FNAME ==="

    # Download
    curl -s -L -o "/tmp/$FNAME" \
      -H "x-api-key: $API_KEY" \
      "https://api.uspto.gov/api/v1/datasets/products/files/PASYR/$FNAME"

    if [ ! -s "/tmp/$FNAME" ]; then
        echo "WARN: Empty file, skipping"
        rm -f "/tmp/$FNAME"
        continue
    fi

    # Unzip (delete zip immediately)
    cd /tmp
    XML_FILE=$(unzip -o "$FNAME" 2>/dev/null | grep "inflating:" | head -1 | sed 's/.*inflating: //' | xargs)
    rm -f "$FNAME"

    if [ -z "$XML_FILE" ] || [ ! -f "/tmp/$XML_FILE" ]; then
        echo "WARN: No XML found, skipping"
        continue
    fi

    # Parse to temp JSONL.gz (overwrite, not append)
    rm -f "$TEMP_JSONL"
    python3 "$PARSER" "/tmp/$XML_FILE" "$TEMP_JSONL" $MIN_YEAR
    rm -f "/tmp/$XML_FILE"

    # Load into BigQuery (appends by default)
    BATCH_SIZE=$(zcat "$TEMP_JSONL" 2>/dev/null | wc -l)
    if [ "$BATCH_SIZE" -gt 0 ]; then
        echo "Loading $BATCH_SIZE records into BigQuery..."
        bq load --project_id=$PROJECT --source_format=NEWLINE_DELIMITED_JSON \
          "$TABLE" "$TEMP_JSONL" 2>&1 | tail -1
        GRAND_TOTAL=$((GRAND_TOTAL + BATCH_SIZE))
    fi
    rm -f "$TEMP_JSONL"

    echo "Running total: $GRAND_TOTAL records | Disk free: $(df -h /tmp | tail -1 | awk '{print $4}')"
done

echo ""
echo "=== COMPLETE ==="
echo "Total records loaded: $GRAND_TOTAL"
