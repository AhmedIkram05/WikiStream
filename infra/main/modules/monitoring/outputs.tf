output "alert_policy_ids" {
  description = "Cloud Monitoring alert policy names (disk-almost-full, vm-unreachable)"
  value = [
    google_monitoring_alert_policy.disk_almost_full.name,
    google_monitoring_alert_policy.vm_unreachable.name,
  ]
}
