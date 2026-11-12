"""L4: the storage module must not accept an unused region argument."""

import re
from pathlib import Path

REPO = Path(__file__).parents[2]
STORAGE = REPO / "infra" / "main" / "modules" / "storage"


def test_storage_module_has_no_unused_region_variable():
    variables = (STORAGE / "variables.tf").read_text()
    assert 'variable "region"' not in variables

    main = (REPO / "infra" / "main" / "main.tf").read_text()
    block = re.search(r'module "storage" \{(.*?)\n\}', main, re.S)
    assert block, "storage module block not found"
    assert "region" not in block.group(1)
