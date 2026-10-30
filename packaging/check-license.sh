#!/bin/sh
# Fail if the licence text or any metadata drifts away from AGPL-3.0-only.
set -eu
cd "$(dirname "$0")/.."
grep -q "GNU AFFERO GENERAL PUBLIC LICENSE" LICENSE
grep -q "Version 3, 19 November 2007" LICENSE
grep -q "AGPL-3.0-only" pyproject.toml
grep -q "AGPL-3.0-only" debian/copyright
! grep -qi "permission is hereby granted" LICENSE debian/copyright
python3 packaging/spdx-headers.py --check
echo "license ok"
