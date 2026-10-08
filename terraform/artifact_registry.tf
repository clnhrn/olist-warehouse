# Keep only the 3 most recent images to stay inside the 0.5 GB free storage.
resource "google_artifact_registry_repository" "olist" {
  repository_id = "olist"
  location      = var.region
  format        = "DOCKER"
  description   = "Container images for the Olist pipeline jobs."

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-latest-3"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }

  cleanup_policies {
    id     = "delete-older"
    action = "DELETE"
    condition {
      tag_state = "ANY"
    }
  }

  depends_on = [google_project_service.apis]
}
