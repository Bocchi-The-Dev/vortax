#!/usr/bin/env python3
"""Ensure every Python source file carries the AGPL SPDX header. `--check` only reports."""
import sys
from pathlib import Path

HEADER = ("# SPDX-License-Identifier: AGPL-3.0-only\n"
          "# Copyright (C) 2026 Vortax contributors. See LICENSE.\n")


def main() -> int:
    check = "--check" in sys.argv
    root = Path(__file__).resolve().parent.parent
    missing = []
    for path in sorted([*(root / "src").rglob("*.py"), *(root / "tests").rglob("*.py")]):
        text = path.read_text(encoding="utf-8")
        if "SPDX-License-Identifier: AGPL-3.0-only" in text:
            continue
        missing.append(path)
        if not check:
            path.write_text(HEADER + text, encoding="utf-8")
    for m in missing:
        print(("missing: " if check else "added: ") + str(m.relative_to(root)))
    return 1 if (check and missing) else 0


if __name__ == "__main__":
    sys.exit(main())
