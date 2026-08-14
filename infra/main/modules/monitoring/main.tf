# Email notifications for all Phase 5 alert policies.
resource "google_monitoring_notification_channel" "alerts" {
  display_name = "WikiStream alerts (email)"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
  project = var.project_id
}

# ch-data is the pipeline's durability surface (vision §7): a 30 GB
# pd-standard volume holding incoming edits. Above 80% for 5+ minutes the
# ClickHouse inserts start failing; free space or grow the disk.
resource "google_monitoring_alert_policy" "disk_almost_full" {
  display_name          = "WikiStream disk almost full (ch-data)"
  project               = var.project_id
  combiner              = "OR"
  enabled               = true
  notification_channels = [google_monitoring_notification_channel.alerts.name]
  user_labels           = var.labels

  documentation {
    content = <<-EOT
      The ch-data volume (30 GB pd-standard, the pipeline's durability surface
      per vision §7) is over 80% full for 5+ minutes. Above this threshold
      ClickHouse inserts begin failing. Free space on the volume or grow the
      disk.
    EOT
  }

  conditions {
    display_name = "ch-data disk usage > 80%"
    condition_threshold {
      filter          = "metric.type=\"agent.googleapis.com/disk/percent_used\" AND resource.type=\"gce_instance\" AND (metric.labels.device=\"ch-data\" OR metric.labels.mount_point=\"/mnt/ch-data\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 80
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
      trigger {
        count = 1
      }
    }
  }
}

# Agentless path: the VM reports this metric itself, so a 120s gap means the
# pipeline is down or the wikistream-vm instance is stopped.
resource "google_monitoring_alert_policy" "vm_unreachable" {
  display_name          = "WikiStream VM unreachable"
  project               = var.project_id
  combiner              = "OR"
  enabled               = true
  notification_channels = [google_monitoring_notification_channel.alerts.name]
  user_labels           = var.labels

  documentation {
    content = <<-EOT
      The wikistream-vm instance stopped reporting
      compute.googleapis.com/instance/uptime for 120s (agentless path). The
      pipeline is down or the VM is stopped.
    EOT
  }

  conditions {
    display_name = "VM uptime metric missing"
    condition_absent {
      filter   = "metric.type=\"compute.googleapis.com/instance/uptime\" AND resource.type=\"gce_instance\""
      duration = "120s"
      # instance/uptime is a DELTA-kind metric — the API rejects an absence
      # condition without a perSeriesAligner (400 on first apply, 2026-08-14).
      aggregations {
        alignment_period   = "120s"
        per_series_aligner = "ALIGN_MEAN"
      }
      trigger {
        count = 1
      }
    }
  }
}
