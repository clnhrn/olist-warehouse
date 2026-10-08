# sa-ingestion-prod: runs the Cloud Run Job. Writes the prod bucket and prod bronze only.
# sa-dev: used by local sessions (impersonation). Writes the dev bucket and dev_* datasets only.
# The CI service account and Workload Identity Federation are added in Phase 8.

resource "google_service_account" "ingestion_prod" {
  account_id   = "sa-ingestion-prod"
  display_name = "Ingestion job (prod)"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "dev" {
  account_id   = "sa-dev"
  display_name = "Local development (dev datasets only)"
  depends_on   = [google_project_service.apis]
}

locals {
  dev_datasets = [for name, d in local.datasets : name if d.env == "dev"]

  job_users = {
    ingestion_prod = google_service_account.ingestion_prod.member
    dev            = google_service_account.dev.member
  }
}

resource "google_project_iam_member" "bq_job_user" {
  for_each = local.job_users

  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = each.value
}

resource "google_storage_bucket_iam_member" "ingestion_prod_lake" {
  bucket = google_storage_bucket.lake["prod"].name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.ingestion_prod.member
}

resource "google_bigquery_dataset_iam_member" "ingestion_prod_bronze" {
  dataset_id = google_bigquery_dataset.layer["bronze"].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.ingestion_prod.member
}

resource "google_storage_bucket_iam_member" "dev_lake" {
  bucket = google_storage_bucket.lake["dev"].name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.dev.member
}

resource "google_bigquery_dataset_iam_member" "dev_datasets" {
  for_each = toset(local.dev_datasets)

  dataset_id = google_bigquery_dataset.layer[each.value].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.dev.member
}

resource "google_service_account_iam_member" "developer_impersonates_dev" {
  service_account_id = google_service_account.dev.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "user:${var.developer_email}"
}
