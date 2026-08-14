resource "google_compute_address" "wikistream_ip" {
  name    = "wikistream-ip"
  region  = var.region
  project = var.project_id
  labels  = var.labels
}

resource "google_compute_instance" "wikistream_vm" {
  name                      = "wikistream-vm"
  machine_type              = "e2-medium"
  zone                      = var.zone
  project                   = var.project_id
  allow_stopping_for_update = true
  labels                    = var.labels

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64" # family renamed upstream (2026): unqualified ubuntu-2404-lts no longer exists
      size  = 50
      type  = "pd-standard"
    }
  }

  network_interface {
    subnetwork = var.subnetwork_self_link

    access_config {
      nat_ip = google_compute_address.wikistream_ip.address
    }
  }

  # Startup script content is injected from main.tf via file() — it must not
  # be referenced here, because file() resolves relative to the declaring
  # module.
  metadata_startup_script = var.startup_script

  metadata = {
    "enable-oslogin" = "TRUE"
  }

  # The ch-data attachment is owned by google_compute_attached_disk.ch_data
  # below. The instance's state still reflects it as an inline attached_disk
  # block (from the first apply of that resource), so ANY in-place update (e.g.
  # a label change) would otherwise show a spurious "remove attached_disk"
  # diff. Pin it off — the standalone resource is the single owner of the
  # attachment. (Known google provider artifact, ignored deliberately.)
  lifecycle {
    ignore_changes = [attached_disk]
  }

  service_account {
    email  = var.service_account_email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}

# Durable data disk: survives every startup.sh-driven instance recreate
# (metadata_startup_script is ForceNew). protected like the tfstate bucket.
# 30 -> 50 on 2026-08-14 (5B.4): ch-data organically hit 93% (2.2G free,
# filling ~3G/2h); grown in place (pd resize is a live update) for headroom
# through 5C + the DEMO chaos battery.
resource "google_compute_disk" "ch_data" {
  name    = "ch-data"
  type    = "pd-standard"
  size    = 50
  zone    = var.zone
  project = var.project_id
  labels  = var.labels

  lifecycle {
    prevent_destroy = true
  }
}

# device_name governs the guest path /dev/disk/by-id/google-ch-data
resource "google_compute_attached_disk" "ch_data" {
  instance    = google_compute_instance.wikistream_vm.name
  disk        = google_compute_disk.ch_data.id
  device_name = "ch-data"
  zone        = var.zone
}

output "instance_name" {
  value = google_compute_instance.wikistream_vm.name
}

output "static_ip" {
  value = google_compute_address.wikistream_ip.address
}
