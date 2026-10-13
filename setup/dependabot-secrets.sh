#!/usr/bin/env bash
# Copy the CI secrets into Dependabot's secret store. Dependabot PRs cannot
# read Actions secrets, so without these their GCP jobs fail on empty values.
# GCP values are looked up with gcloud (names match terraform/ci.tf and
# setup/bootstrap-gcp.sh); only the two emails are asked for, with defaults.
# Prerequisites: gh auth login && gcloud auth login
# Usage: ./setup/dependabot-secrets.sh [project-id]
#   project-id defaults to the active gcloud project.
# Safe to rerun: every secret is overwritten with the same values.
set -euo pipefail

log() { echo "==> $*"; }
die() { echo "error: $*" >&2; exit 1; }

if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [project-id]" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PROJECT_ID="${1:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT_ID" ]] || die "no project id; pass one or run: gcloud config set project <id>"

log "Looking up values in $PROJECT_ID."

TF_STATE_BUCKET="${PROJECT_ID}-tfstate"
gcloud storage buckets describe "gs://$TF_STATE_BUCKET" --project="$PROJECT_ID" >/dev/null \
  || die "state bucket gs://$TF_STATE_BUCKET not found"

WIF_PROVIDER="$(gcloud iam workload-identity-pools providers describe github \
  --workload-identity-pool=github --location=global --project="$PROJECT_ID" \
  --format='value(name)')"
[[ "$WIF_PROVIDER" =~ ^projects/[0-9]+/ ]] || die "unexpected WIF provider name: $WIF_PROVIDER"

sa_email() {
  local email="$1@${PROJECT_ID}.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "$email" --project="$PROJECT_ID" >/dev/null \
    || die "service account $email not found"
  echo "$email"
}
CI_PR_SA="$(sa_email sa-ci-pr)"
CI_DEPLOY_SA="$(sa_email sa-ci-deploy)"

# The emails are Terraform variables, not GCP resources, so ask for them.
default_email="$(gcloud config get-value account 2>/dev/null)"
read -rp "DEVELOPER_EMAIL [$default_email]: " DEVELOPER_EMAIL
DEVELOPER_EMAIL="${DEVELOPER_EMAIL:-$default_email}"
read -rp "ALERT_EMAIL [$DEVELOPER_EMAIL]: " ALERT_EMAIL
ALERT_EMAIL="${ALERT_EMAIL:-$DEVELOPER_EMAIL}"
[[ -n "$DEVELOPER_EMAIL" ]] || die "DEVELOPER_EMAIL is empty"

declare -A VALUES=(
  [GCP_PROJECT_ID]="$PROJECT_ID"
  [TF_STATE_BUCKET]="$TF_STATE_BUCKET"
  [WIF_PROVIDER]="$WIF_PROVIDER"
  [CI_PR_SA]="$CI_PR_SA"
  [CI_DEPLOY_SA]="$CI_DEPLOY_SA"
  [DEVELOPER_EMAIL]="$DEVELOPER_EMAIL"
  [ALERT_EMAIL]="$ALERT_EMAIL"
)

echo
for name in "${!VALUES[@]}"; do
  printf '  %-16s %s\n' "$name" "${VALUES[$name]}"
done
echo
read -rp "Set these as Dependabot secrets? [y/N] " answer
[[ "$answer" == [yY] ]] || die "aborted, nothing set"

for name in "${!VALUES[@]}"; do
  # Pipe through stdin (printf is a builtin) so the value never shows in ps.
  printf '%s' "${VALUES[$name]}" | gh secret set "$name" --app dependabot
done

echo
log "Dependabot secrets now set:"
gh secret list --app dependabot
