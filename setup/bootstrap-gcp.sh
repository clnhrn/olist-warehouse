#!/usr/bin/env bash
# One-time GCP bootstrap: the minimum Terraform needs before it can run.
# Everything else (other APIs, service accounts, WIF, datasets) is Terraform.
# Prerequisites: gcloud auth login && gcloud auth application-default login
# Usage: ./setup/bootstrap-gcp.sh <project-id> <billing-account-id>
# Safe to rerun: each step skips work that is already done.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <project-id> <billing-account-id>" >&2
  echo "Find the billing account id with: gcloud billing accounts list" >&2
  exit 1
fi

PROJECT_ID="$1"
BILLING_ACCOUNT="$2"
LOCATION="us-central1"
STATE_BUCKET="gs://${PROJECT_ID}-tfstate"
BOOTSTRAP_APIS=(
  serviceusage.googleapis.com
  cloudresourcemanager.googleapis.com
  storage.googleapis.com
)

log() { echo "[bootstrap-gcp] $*"; }

if [[ -z "$(gcloud auth list --filter=status:ACTIVE --format='value(account)')" ]]; then
  echo "Not logged in. Run: gcloud auth login" >&2
  exit 1
fi
if [[ ! -f "$HOME/.config/gcloud/application_default_credentials.json" ]]; then
  echo "No application default credentials. Run: gcloud auth application-default login" >&2
  exit 1
fi

if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  log "Project $PROJECT_ID exists, skipping create."
else
  log "Creating project $PROJECT_ID."
  gcloud projects create "$PROJECT_ID"
fi

if [[ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" == "True" ]]; then
  log "Billing already linked, skipping."
else
  log "Linking billing account."
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
fi

gcloud config set project "$PROJECT_ID"
gcloud auth application-default set-quota-project "$PROJECT_ID"

log "Enabling bootstrap APIs."
gcloud services enable "${BOOTSTRAP_APIS[@]}" --project="$PROJECT_ID"

if gcloud storage buckets describe "$STATE_BUCKET" --project="$PROJECT_ID" >/dev/null 2>&1; then
  log "State bucket $STATE_BUCKET exists, skipping create."
else
  log "Creating state bucket $STATE_BUCKET."
  gcloud storage buckets create "$STATE_BUCKET" \
    --project="$PROJECT_ID" \
    --location="$LOCATION" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi
gcloud storage buckets update "$STATE_BUCKET" --project="$PROJECT_ID" --versioning

log "Done. Project: $PROJECT_ID, state bucket: $STATE_BUCKET"
log "Next: create the \$5 budget alert in the console if you have not yet."
