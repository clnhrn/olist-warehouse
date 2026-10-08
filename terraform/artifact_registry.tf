# Keep the 2 most recent versions per image (current plus one to roll back to)
# to stay near the 0.5 GB free storage. Layers are stored once per repository,
# so versions with the same uv.lock share their dependency layer.
resource "google_artifact_registry_repository" "olist" {
  repository_id = "olist"
  location      = var.region
  format        = "DOCKER"
  description   = "Container images for the Olist pipeline jobs."

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-latest-2"
    action = "KEEP"
    most_recent_versions {
      keep_count = 2
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
