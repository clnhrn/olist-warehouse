# One lake bucket per environment, so IAM stays per bucket with no prefix conditions.
# Layout: landing/load_date=D/, bronze/<table>/load_date=D/, _manifests/.
resource "google_storage_bucket" "lake" {
  for_each = local.envs

  name                        = "${var.project_id}-lake-${each.key}"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  labels = {
    env = each.key
  }
}
