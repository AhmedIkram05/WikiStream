resource "google_compute_network" "wikistream_vpc" {
  name                    = "wikistream-vpc"
  auto_create_subnetworks = false
  project                 = var.project_id
}

resource "google_compute_subnetwork" "wikistream_subnet" {
  name          = "wikistream-subnet"
  ip_cidr_range = "10.0.0.0/24"
  region        = var.region
  network       = google_compute_network.wikistream_vpc.id
  project       = var.project_id
}

# allow-internal: everything inside the VPC may talk to everything else
# (compose inter-service traffic, e.g. consumer -> clickhouse).
resource "google_compute_firewall" "allow_internal" {
  name    = "allow-internal"
  network = google_compute_network.wikistream_vpc.id
  project = var.project_id

  allow {
    protocol = "all"
  }

  source_ranges = ["10.0.0.0/24"]
}

resource "google_compute_firewall" "allow_ssh" {
  name    = "allow-ssh"
  network = google_compute_network.wikistream_vpc.id
  project = var.project_id

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = var.allowed_ips
}

resource "google_compute_firewall" "allow_grafana" {
  name    = "allow-grafana"
  network = google_compute_network.wikistream_vpc.id
  project = var.project_id

  allow {
    protocol = "tcp"
    ports    = ["3000"]
  }

  source_ranges = var.allowed_ips
}

resource "google_compute_firewall" "allow_clickhouse" {
  name    = "allow-clickhouse"
  network = google_compute_network.wikistream_vpc.id
  project = var.project_id

  allow {
    protocol = "tcp"
    ports    = ["8123"]
  }

  source_ranges = var.allowed_ips
}

output "subnetwork_self_link" {
  value = google_compute_subnetwork.wikistream_subnet.self_link
}
