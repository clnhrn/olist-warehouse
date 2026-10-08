# Email alerts when the pipeline fails. Cloud Monitoring alerting on these
# built-in metrics is free. A failed job also fails the workflow, so one
# failure usually sends two emails; the workflow alert alone also catches
# failures outside the jobs (for example, a permission error).

resource "google_monitoring_notification_channel" "email" {
  display_name = "Pipeline alerts"
  type         = "email"

  labels = {
    email_address = coalesce(var.alert_email, var.developer_email)
  }

  depends_on = [google_project_service.apis]
}

resource "google_monitoring_alert_policy" "job_failed" {
  display_name          = "Cloud Run job execution failed"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "Failed job executions"

    condition_threshold {
      filter          = "resource.type = \"cloud_run_job\" AND metric.type = \"run.googleapis.com/job/completed_execution_count\" AND metric.labels.result = \"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.labels.job_name"]
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    content   = "A Cloud Run job execution failed. Check `gcloud run jobs executions list --region=${var.region}` and the job logs; rows written before the failure are in ops.pipeline_runs with status failed."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "workflow_failed" {
  display_name          = "Pipeline workflow failed"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "Failed workflow executions"

    condition_threshold {
      filter          = "resource.type = \"workflows.googleapis.com/Workflow\" AND metric.type = \"workflows.googleapis.com/finished_execution_count\" AND metric.labels.status = \"FAILED\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.labels.workflow_id"]
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    content   = "The pipeline workflow failed. Check `gcloud workflows executions list pipeline --location=${var.region}`."
    mime_type = "text/markdown"
  }
}
