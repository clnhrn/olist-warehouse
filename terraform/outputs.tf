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

output "agent_service_accounts" {
  description = "Agent account per environment. Impersonate the dev one for local agent runs."
  value       = { for env, sa in google_service_account.agent : env => sa.email }
}

output "image_repository" {
  description = "Docker repository for pipeline images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.olist.repository_id}"
}

output "wif_provider" {
  description = "Workload Identity provider for google-github-actions/auth."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "ci_pr_service_account" {
  description = "Service account for pull request CI."
  value       = google_service_account.ci_pr.email
}

output "ci_deploy_service_account" {
  description = "Service account for deploys on merge to main."
  value       = google_service_account.ci_deploy.email
}

output "pipeline_workflow" {
  description = "Run the pipeline by hand: gcloud workflows run <name> --location=<region>."
  value       = local.pipeline_ready ? google_workflows_workflow.pipeline[0].name : null
}
