"""AUD-17: the example tfvars must pass the CIDR validation."""

import ipaddress
import re
from pathlib import Path

REPO = Path(__file__).parents[2]
EXAMPLE = REPO / "infra" / "main" / "terraform.tfvars.example"


def test_allowed_ips_example_is_a_valid_cidr():
    text = EXAMPLE.read_text()
    match = re.search(r'allowed_ips\s*=\s*\["([^"]+)"\]', text)
    assert match, "allowed_ips example not found"
    ipaddress.ip_network(match.group(1), strict=False)
