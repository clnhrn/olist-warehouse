# Daily pipeline: Cloud Scheduler -> Workflow -> ingestion job -> dbt job.
# Created once both job images exist. sa-orchestrator-prod runs the workflow
# and is the identity the schedule calls it with; it can start the two jobs
# and nothing else.

locals {
  pipeline_ready = var.ingestion_image != null && var.dbt_image != null
  pipeline_jobs  = local.pipeline_ready ? toset(["ingestion", "dbt"]) : toset([])
}

resource "google_service_account" "orchestrator_prod" {
  account_id   = "sa-orchestrator-prod"
  display_name = "Pipeline workflow and schedule (prod)"
  depends_on   = [google_project_service.apis]
}

# The workflow passes its execution id to each job as an env override
# (OLIST_RUN_ID), which needs run.jobs.runWithOverrides; roles/run.invoker
# has only run.jobs.run.
resource "google_cloud_run_v2_job_iam_member" "orchestrator_runs_jobs" {
  for_each = local.pipeline_jobs

  name     = each.value
  location = var.region
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = google_service_account.orchestrator_prod.member

  depends_on = [google_cloud_run_v2_job.ingestion, google_cloud_run_v2_job.dbt]
}

# The Workflows connector waits for a job by polling its long-running
# operation, which needs run.operations.get. Operations are project-level, so
# a one-permission custom role is the narrowest grant (roles/run.viewer would
# also allow reading every Cloud Run resource).
resource "google_project_iam_custom_role" "run_operations_viewer" {
  role_id     = "runOperationsViewer"
  title       = "Cloud Run operations viewer"
  description = "Poll Cloud Run long-running operations (Workflows job connector)."
  permissions = ["run.operations.get"]
}

resource "google_project_iam_member" "orchestrator_run_operations" {
  project = var.project_id
  role    = google_project_iam_custom_role.run_operations_viewer.id
  member  = google_service_account.orchestrator_prod.member
}

# Workflows has no per-workflow IAM, so these are project-level.
resource "google_project_iam_member" "orchestrator" {
  for_each = toset(["roles/workflows.invoker", "roles/logging.logWriter"])

  project = var.project_id
  role    = each.value
  member  = google_service_account.orchestrator_prod.member
}

resource "google_workflows_workflow" "pipeline" {
  count = local.pipeline_ready ? 1 : 0

  name                = "pipeline"
  region              = var.region
  description         = "Ingestion job, then dbt job."
  service_account     = google_service_account.orchestrator_prod.id
  call_log_level      = "LOG_ERRORS_ONLY"
  deletion_protection = false

  source_contents = templatefile("${path.module}/workflows/pipeline.yaml", {
    project_id    = var.project_id
    region        = var.region
    ingestion_job = google_cloud_run_v2_job.ingestion[0].name
    dbt_job       = google_cloud_run_v2_job.dbt[0].name
  })

  depends_on = [google_project_service.apis]
}

# Safe to run daily: with no new landing date, ingestion skips every table and
# dbt merges nothing.
resource "google_cloud_scheduler_job" "pipeline_daily" {
  count = local.pipeline_ready ? 1 : 0

  name        = "pipeline-daily"
  region      = var.region
  description = "Runs the pipeline workflow every morning."
  schedule    = "0 6 * * *"
  time_zone   = "America/Sao_Paulo"

  # No retry_config: the default is no retries, and the API omits a block
  # with only defaults, so declaring it shows a diff on every plan.

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/${google_workflows_workflow.pipeline[0].id}/executions"
    body        = base64encode("{}")

    oauth_token {
      service_account_email = google_service_account.orchestrator_prod.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_project_service.apis]
}
