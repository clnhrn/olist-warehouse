# GitHub Actions authenticates with Workload Identity Federation: GitHub's
# short-lived OIDC token is exchanged for a GCP token, so no key files exist.
#
# sa-ci-pr: pull requests. Reads the project (terraform plan) and writes the
#   dev_* datasets only (dbt build --target dev). Any ref in the repo.
# sa-ci-deploy: merges to main. Applies Terraform, pushes images, updates and
#   runs the jobs. Only refs/heads/main can use it.

data "google_project" "this" {}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  description               = "OIDC tokens from GitHub Actions in ${var.github_repository}."

  depends_on = [google_project_service.apis]
}

# The numeric repository id cannot be reused by another repo, unlike its name.
resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  # repository_ref ("<repo id>:<git ref>") is what the deploy binding matches,
  # instead of the subject: GitHub's subject format varies (this repo uses the
  # immutable form, repo:<owner>@<id>/<name>@<id>:ref:...).
  attribute_mapping = {
    "google.subject"           = "assertion.sub"
    "attribute.repository"     = "assertion.repository"
    "attribute.repository_id"  = "assertion.repository_id"
    "attribute.repository_ref" = "assertion.repository_id + ':' + assertion.ref"
  }
  attribute_condition = "assertion.repository_id == \"${var.github_repository_id}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

locals {
  wif_pool = "iam.googleapis.com/${google_iam_workload_identity_pool.github.name}"

  # A push or dispatch on main. Pull requests have the ref
  # refs/pull/<n>/merge, so they never match.
  main_branch_principal = "principalSet://${local.wif_pool}/attribute.repository_ref/${var.github_repository_id}:refs/heads/main"
  repo_principal_set    = "principalSet://${local.wif_pool}/attribute.repository_id/${var.github_repository_id}"

  # Created by setup/bootstrap-gcp.sh, outside Terraform.
  state_bucket = "${var.project_id}-tfstate"

  ci_deploy_roles = [
    "roles/artifactregistry.admin",
    "roles/bigquery.admin",
    "roles/cloudscheduler.admin",
    "roles/iam.roleAdmin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/iam.workloadIdentityPoolAdmin",
    "roles/monitoring.editor",
    "roles/resourcemanager.projectIamAdmin",
    "roles/run.admin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/storage.admin",
    "roles/workflows.admin",
  ]
}

resource "google_service_account" "ci_pr" {
  account_id   = "sa-ci-pr"
  display_name = "CI for pull requests (read-only, dev datasets)"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "ci_deploy" {
  account_id   = "sa-ci-deploy"
  display_name = "CI deploy on merge to main"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account_iam_member" "github_uses_ci_pr" {
  service_account_id = google_service_account.ci_pr.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.repo_principal_set
}

resource "google_service_account_iam_member" "main_uses_ci_deploy" {
  service_account_id = google_service_account.ci_deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.main_branch_principal
}

# terraform plan reads every resource and IAM policy. Basic Viewer also reads
# prod table data through the legacy dataset ACL (read-only, accepted here).
resource "google_project_iam_member" "ci_pr" {
  for_each = toset(["roles/viewer", "roles/iam.securityReviewer", "roles/bigquery.jobUser"])

  project = var.project_id
  role    = each.value
  member  = google_service_account.ci_pr.member
}

# Read only: PR plans run with -lock=false, so they never write the lock file.
resource "google_storage_bucket_iam_member" "ci_pr_reads_state" {
  bucket = local.state_bucket
  role   = "roles/storage.objectViewer"
  member = google_service_account.ci_pr.member
}

resource "google_bigquery_dataset_iam_member" "ci_pr_dev_datasets" {
  for_each = toset(local.dev_datasets)

  dataset_id = google_bigquery_dataset.layer[each.value].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.ci_pr.member
}

# projectIamAdmin lets this account grant itself more, so the real protection
# is that only main can use it.
resource "google_project_iam_member" "ci_deploy" {
  for_each = toset(local.ci_deploy_roles)

  project = var.project_id
  role    = each.value
  member  = google_service_account.ci_deploy.member
}
