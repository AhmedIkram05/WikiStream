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

# 5C.2: delete GCP's permissive default firewall rules (world-open SSH/RDP/ICMP
# + over-broad internal). Idempotent: `|| true` + 2>/dev/null swallow the
# already-deleted / already-absent case so re-applies are no-ops. Custom rules
# above remain the only ingress.
resource "null_resource" "disable_default_firewall_rules" {
  triggers = { rules = "default-allow-ssh,default-allow-rdp,default-allow-icmp,default-allow-internal" }

  provisioner "local-exec" {
    command = "gcloud compute firewall-rules delete default-allow-ssh default-allow-rdp default-allow-icmp default-allow-internal --quiet --project=${var.project_id} 2>/dev/null || true"
  }
}

output "subnetwork_self_link" {
  value = google_compute_subnetwork.wikistream_subnet.self_link
}
