#!/usr/bin/env bash
# Save your Kaggle API token to ~/.kaggle/access_token without echoing it.
# The token never touches the repo or shell history.
# Usage: ./setup/kaggle-token.sh   (then paste the token and press Enter)
set -euo pipefail

TOKEN_FILE="$HOME/.kaggle/access_token"

read -rsp "Paste Kaggle API token: " token
echo
if [[ -z "$token" ]]; then
  echo "No token entered." >&2
  exit 1
fi

mkdir -p "$HOME/.kaggle"
chmod 700 "$HOME/.kaggle"
# umask so the file is never readable by others, even briefly.
(umask 077 && printf '%s\n' "$token" > "$TOKEN_FILE")
unset token

echo "Saved to $TOKEN_FILE"
