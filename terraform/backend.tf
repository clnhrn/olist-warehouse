# Partial backend config: bucket and prefix come from backend.hcl (gitignored).
# terraform -chdir=terraform init -backend-config=backend.hcl
terraform {
  backend "gcs" {}
}
