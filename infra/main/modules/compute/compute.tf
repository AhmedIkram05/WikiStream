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
      image = "ubuntu-os-cloud/ubuntu-2404-lts"
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

  service_account {
    email  = var.service_account_email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}

output "instance_name" {
  value = google_compute_instance.wikistream_vm.name
}

output "static_ip" {
  value = google_compute_address.wikistream_ip.address
}
