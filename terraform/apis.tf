# Bootstrap APIs (serviceusage, cloudresourcemanager, storage) are enabled by
# setup/bootstrap-gcp.sh. STS exchanges GitHub's OIDC token (ci.tf).
locals {
  apis = [
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sts.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)

  service            = each.value
  disable_on_destroy = false
}
