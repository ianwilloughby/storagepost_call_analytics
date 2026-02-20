#!/bin/bash
# Package each Lambda function into a zip for Terraform deployment
# Usage: bash lambdas/build.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/../terraform/lambda_packages"
mkdir -p "$OUTPUT_DIR"

FUNCTIONS=("stream_processor" "athena_executor" "api_handler")

for FUNC in "${FUNCTIONS[@]}"; do
  echo "Building $FUNC..."
  FUNC_DIR="$SCRIPT_DIR/$FUNC"
  TMP_DIR=$(mktemp -d)

  # Copy source files
  cp -r "$FUNC_DIR"/*.py "$TMP_DIR/" 2>/dev/null || true
  cp -r "$FUNC_DIR"/*.txt "$TMP_DIR/" 2>/dev/null || true

  # Install dependencies
  if [ -f "$FUNC_DIR/requirements.txt" ]; then
    pip3 install -r "$FUNC_DIR/requirements.txt" -t "$TMP_DIR/" --quiet
  fi

  # Zip
  OUTPUT="$OUTPUT_DIR/${FUNC}.zip"
  (cd "$TMP_DIR" && zip -r "$OUTPUT" . -q)
  rm -rf "$TMP_DIR"

  echo "  → $OUTPUT ($(du -sh "$OUTPUT" | cut -f1))"
done

echo "All Lambda packages built in $OUTPUT_DIR/"
