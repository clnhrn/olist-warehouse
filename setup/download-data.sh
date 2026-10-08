#!/usr/bin/env bash
# Download the Olist dataset from Kaggle into data/raw and check all 9 CSVs.
# Prerequisite: ./setup/kaggle-token.sh
# Usage: ./setup/download-data.sh [--force]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW_DIR="$REPO_ROOT/data/raw"
EXPECTED_FILES=(
  olist_customers_dataset.csv
  olist_geolocation_dataset.csv
  olist_order_items_dataset.csv
  olist_order_payments_dataset.csv
  olist_order_reviews_dataset.csv
  olist_orders_dataset.csv
  olist_products_dataset.csv
  olist_sellers_dataset.csv
  product_category_name_translation.csv
)

log() { echo "[download-data] $*"; }

missing_files() {
  local f
  for f in "${EXPECTED_FILES[@]}"; do
    [[ -f "$RAW_DIR/$f" ]] || echo "$f"
  done
}

if [[ "${1:-}" != "--force" && -z "$(missing_files)" ]]; then
  log "All 9 CSVs already in $RAW_DIR, skipping. Use --force to redownload."
  exit 0
fi

mkdir -p "$RAW_DIR"
kaggle datasets download -d olistbr/brazilian-ecommerce -p "$RAW_DIR" --unzip --force

missing="$(missing_files)"
if [[ -n "$missing" ]]; then
  echo "Missing after download:" >&2
  echo "$missing" >&2
  exit 1
fi
log "All 9 CSVs present in $RAW_DIR."
