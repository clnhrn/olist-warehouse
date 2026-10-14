# Text-to-SQL agent (Phase 9). IAM is the real guardrail: each agent account
# can read its env's gold dataset, append to one log table, and read the LLM
# API key. Nothing else, so a bad query is refused even if it passes the SQL
# checks in agent/guard.py.
#
# sa-agent-dev: local sessions (impersonation) and the CI eval job.
# sa-agent-prod: only the Cloud Run UI service (agent-ui, below).

locals {
  # Same naming as the datasets: prod uses the layer name, dev adds dev_.
  agent_envs = {
    for env in local.envs : env => {
      gold = env == "prod" ? "gold" : "dev_gold"
      ops  = env == "prod" ? "ops" : "dev_ops"
    }
  }
}

resource "google_service_account" "agent" {
  for_each = local.agent_envs

  account_id   = "sa-agent-${each.key}"
  display_name = "Text-to-SQL agent (${each.key}, read-only gold)"
  depends_on   = [google_project_service.apis]
}

# Runs query jobs. Grants no data access by itself.
resource "google_project_iam_member" "agent_job_user" {
  for_each = local.agent_envs

  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = google_service_account.agent[each.key].member
}

resource "google_bigquery_dataset_iam_member" "agent_reads_gold" {
  for_each = local.agent_envs

  dataset_id = google_bigquery_dataset.layer[each.value.gold].dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = google_service_account.agent[each.key].member
}

# One row per question. Owned by Terraform with an explicit schema, so the
# agent needs write access to this table only, not to the ops dataset.
resource "google_bigquery_table" "agent_queries" {
  for_each = local.agent_envs

  dataset_id          = google_bigquery_dataset.layer[each.value.ops].dataset_id
  table_id            = "agent_queries"
  description         = "Text-to-SQL agent log: one row per question, with the SQL, cost, and outcome."
  deletion_protection = true

  time_partitioning {
    type  = "DAY"
    field = "asked_at"
  }

  schema = jsonencode([
    { name = "query_id", type = "STRING", mode = "REQUIRED", description = "Unique id of this question." },
    { name = "session_id", type = "STRING", mode = "NULLABLE", description = "UI or CLI session, to group a conversation." },
    { name = "asked_at", type = "TIMESTAMP", mode = "REQUIRED", description = "When the question was asked." },
    { name = "target", type = "STRING", mode = "REQUIRED", description = "Warehouse queried: dev or prod." },
    { name = "question", type = "STRING", mode = "REQUIRED", description = "The question as asked." },
    { name = "sql", type = "STRING", mode = "NULLABLE", description = "Last SQL the agent ran, after the guard's rewrite." },
    { name = "status", type = "STRING", mode = "REQUIRED", description = "answered, refused, or failed." },
    { name = "error", type = "STRING", mode = "NULLABLE", description = "Guard, BigQuery, or LLM error, truncated." },
    { name = "bytes_billed", type = "INTEGER", mode = "NULLABLE", description = "BigQuery bytes billed, summed over attempts." },
    { name = "rows_returned", type = "INTEGER", mode = "NULLABLE", description = "Rows in the final result." },
    { name = "latency_ms", type = "INTEGER", mode = "NULLABLE", description = "Time from question to answer." },
    { name = "model", type = "STRING", mode = "NULLABLE", description = "LLM model id." },
    { name = "input_tokens", type = "INTEGER", mode = "NULLABLE", description = "LLM input tokens, summed over calls." },
    { name = "output_tokens", type = "INTEGER", mode = "NULLABLE", description = "LLM output tokens, summed over calls." },
    { name = "attempts", type = "INTEGER", mode = "NULLABLE", description = "SQL attempts, including retries after errors." },
  ])
}

resource "google_bigquery_table_iam_member" "agent_writes_log" {
  for_each = local.agent_envs

  dataset_id = google_bigquery_table.agent_queries[each.key].dataset_id
  table_id   = google_bigquery_table.agent_queries[each.key].table_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.agent[each.key].member
}

# The value is never in Terraform (it would land in the state). Add it once:
#   printf %s "$KEY" | gcloud secrets versions add anthropic-api-key --data-file=-
resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "anthropic-api-key"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  # sa-ci-deploy needs secretmanager.admin (ci.tf) before it can create this.
  depends_on = [google_project_service.apis, google_project_iam_member.ci_deploy]
}

resource "google_secret_manager_secret_iam_member" "agent_reads_api_key" {
  for_each = local.agent_envs

  secret_id = google_secret_manager_secret.anthropic_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.agent[each.key].member
}

# Local sessions run the agent as sa-agent-dev, never as sa-agent-prod.
resource "google_service_account_iam_member" "developer_impersonates_agent_dev" {
  service_account_id = google_service_account.agent["dev"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "user:${var.developer_email}"
}

# The manual eval workflow authenticates as sa-ci-pr, then runs its queries
# as sa-agent-dev, so evals get exactly the agent's access.
resource "google_service_account_iam_member" "ci_pr_impersonates_agent_dev" {
  service_account_id = google_service_account.agent["dev"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.ci_pr.member
}

# Chainlit chat UI, private. Created only after the first agent image is pushed
# (var.agent_image). After that, CI owns the image (gcloud run services
# update --image), so Terraform ignores it, like the jobs.
# No allUsers invoker: only the developer may call it, through
#   gcloud run services proxy agent-ui --region us-central1
resource "google_cloud_run_v2_service" "agent_ui" {
  count = var.agent_image == null ? 0 : 1

  name                = "agent-ui"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.agent["prod"].email
    timeout         = "300s"
    # Chainlit keeps one websocket per browser session; one small instance
    # is plenty for a private demo, and zero when idle costs nothing.
    max_instance_request_concurrency = 20

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = var.agent_image

      ports {
        container_port = 8080
      }

      env {
        name  = "OLIST_PROJECT_ID"
        value = var.project_id
      }

      resources {
        cpu_idle = true
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [client, client_version, template[0].containers[0].image]
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "developer_invokes_agent_ui" {
  count = var.agent_image == null ? 0 : 1

  name     = google_cloud_run_v2_service.agent_ui[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "user:${var.developer_email}"
}
