#!/usr/bin/env bash
# Install the local CLI tools inside WSL/Ubuntu: gcloud, Terraform, Kaggle.
# Run once per machine. Safe to rerun: installed tools are skipped.
# Usage: ./setup/install-tools.sh
set -euo pipefail

log() { echo "[install-tools] $*"; }

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates gnupg curl

# gcloud: check the Linux install specifically. A Windows gcloud on PATH
# (/mnt/c/...) stores credentials WSL Python cannot see.
if [[ -x /usr/bin/gcloud ]]; then
  log "gcloud already installed, skipping."
else
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | sudo gpg --dearmor --yes -o /usr/share/keyrings/cloud.google.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y google-cloud-cli
fi

if command -v terraform >/dev/null 2>&1; then
  log "terraform already installed, skipping."
else
  # shellcheck source=/dev/null
  codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  curl -fsSL https://apt.releases.hashicorp.com/gpg \
    | sudo gpg --dearmor --yes -o /usr/share/keyrings/hashicorp.gpg
  echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com ${codename} main" \
    | sudo tee /etc/apt/sources.list.d/hashicorp.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y terraform
fi

# --upgrade: older Kaggle CLIs do not read ~/.kaggle/access_token.
uv tool install --upgrade kaggle

log "gcloud:    $(command -v gcloud)"
log "terraform: $(terraform version | head -n 1)"
log "kaggle:    $(kaggle --version)"
if [[ "$(command -v gcloud)" != /usr/bin/gcloud ]]; then
  log "WARNING: 'gcloud' resolves to $(command -v gcloud), not /usr/bin/gcloud. Fix PATH order."
fi
