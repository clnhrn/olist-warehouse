variable "project_id" {
  description = "GCP project id."
  type        = string
}

variable "region" {
  description = "Region for every resource. us-central1 keeps GCS in the always-free tier."
  type        = string
  default     = "us-central1"
}

variable "developer_email" {
  description = "Your Google account. Gets permission to impersonate the dev service account."
  type        = string
}

variable "alert_email" {
  description = "Where pipeline failure alerts go. Defaults to developer_email."
  type        = string
  default     = null
}

variable "github_repository" {
  description = "GitHub repo (owner/name) whose Actions may use the CI service accounts."
  type        = string
  default     = "clnhrn/olist-warehouse"
}

variable "github_repository_id" {
  description = "Numeric id of that repo (gh api repos/<owner>/<name> --jq .id). Public, not a secret."
  type        = string
  default     = "1409552597"
}

variable "ingestion_image" {
  description = "Full image URI for the ingestion Cloud Run Job. Leave null until the image is pushed."
  type        = string
  default     = null
}

variable "dbt_image" {
  description = "Full image URI for the dbt Cloud Run Job. Leave null until the image is pushed."
  type        = string
  default     = null
}
