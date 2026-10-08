output "lake_buckets" {
  description = "Lake bucket per environment."
  value       = { for env, b in google_storage_bucket.lake : env => b.name }
}

output "datasets" {
  description = "BigQuery datasets."
  value       = sort(keys(google_bigquery_dataset.layer))
}

output "dev_service_account" {
  description = "Impersonate this for local work against dev."
  value       = google_service_account.dev.email
}

output "image_repository" {
  description = "Docker repository for pipeline images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.olist.repository_id}"
}

output "pipeline_workflow" {
  description = "Run the pipeline by hand: gcloud workflows run <name> --location=<region>."
  value       = local.pipeline_ready ? google_workflows_workflow.pipeline[0].name : null
}
