# Setup

One-time setup for a new machine and a new GCP project. Run everything from the repo root, inside WSL.
Every script is safe to rerun: steps that are already done are skipped.

## 1. Install tools (once per machine)

```bash
./setup/install-tools.sh
```

Installs gcloud (the Linux version, not the Windows one), Terraform, and the Kaggle CLI. Requires `uv`.

## 2. Log in to GCP (manual, opens a browser)

```bash
gcloud auth login
gcloud auth application-default login   # credentials for Terraform and Python libraries
```

## 3. Bootstrap the GCP project (once per project)

```bash
gcloud billing accounts list              # copy the ACCOUNT_ID
./setup/bootstrap-gcp.sh <project-id> <billing-account-id>
```

Creates the project, links billing, enables the three APIs Terraform needs to start, and creates the versioned state bucket `gs://<project-id>-tfstate` in `us-central1`. Everything else is created by Terraform.

## 4. Budget alert (manual, in the console)

Billing → Budgets & alerts → Create budget. Scope: this project. Amount: $5. Alerts at 50%, 90%, and 100%.
A budget only sends alerts. It does not cap spending.

## 5. Kaggle token and dataset

```bash
./setup/kaggle-token.sh       # paste the token from kaggle.com → Settings → API → Generate New Token
./setup/download-data.sh      # downloads into data/raw and checks all 9 CSVs
```

The token is saved to `~/.kaggle/access_token`, outside the repo. Never put it in a file in this repo.

## 6. Dependabot secrets (after the first Terraform apply)

```bash
./setup/dependabot-secrets.sh [project-id]   # defaults to the active gcloud project
```

Dependabot PRs cannot read the repo's Actions secrets, so without this their GCP checks fail. The script looks up the project, state bucket, WIF provider, and CI service accounts with gcloud, asks for the two emails, shows all values, and sets them after you confirm. See [security-and-ci.md](../docs/security-and-ci.md#bootstrap).
