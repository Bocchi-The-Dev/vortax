#!/usr/bin/env python3
"""Print the CHANGELOG.md section for a version (e.g. 3.1.0b1), for release notes."""
import re
import sys
from pathlib import Path

version = sys.argv[1].lstrip("v")
text = Path(__file__).resolve().parent.parent.joinpath("CHANGELOG.md").read_text(encoding="utf-8")
m = re.search(rf"^## {re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, re.S | re.M)
if not m:
    sys.exit(f"CHANGELOG.md has no section for {version}")
print(m.group(1).strip())
