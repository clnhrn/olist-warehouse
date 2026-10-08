# Bootstrap APIs (serviceusage, cloudresourcemanager, storage) are enabled by
# setup/bootstrap-gcp.sh. Scheduler, Workflows, and STS are added in later phases.
locals {
  apis = [
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "run.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)

  service            = each.value
  disable_on_destroy = false
}
