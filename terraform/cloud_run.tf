# Each job is created only after its first image is pushed (var.*_image).
# The ingestion job's default args ingest every landed date (the scheduled run).
# One date by hand:
#   gcloud run jobs execute ingestion --args=ingest,--env,prod,--sink,gcs,--load-date,YYYY-MM-DD
resource "google_cloud_run_v2_job" "ingestion" {
  count = var.ingestion_image == null ? 0 : 1

  name                = "ingestion"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.ingestion_prod.email
      max_retries     = 1
      timeout         = "1800s"

      containers {
        image = var.ingestion_image
        args  = ["ingest", "--env", "prod", "--sink", "gcs", "--all-dates"]

        env {
          name  = "OLIST_PROJECT_ID"
          value = var.project_id
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "2Gi"
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# The image's default command is dbt build --target prod (without unit tests).
# Retrying is safe: silver merges on keys, so a rerun changes no row counts.
resource "google_cloud_run_v2_job" "dbt" {
  count = var.dbt_image == null ? 0 : 1

  name                = "dbt"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.dbt_prod.email
      max_retries     = 1
      timeout         = "1800s"

      containers {
        image = var.dbt_image

        env {
          name  = "OLIST_PROJECT_ID"
          value = var.project_id
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}
