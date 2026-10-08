# Created only after the first image is pushed (set var.ingestion_image).
# The load date is passed per execution:
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
        args  = ["ingest", "--env", "prod", "--sink", "gcs"]

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
