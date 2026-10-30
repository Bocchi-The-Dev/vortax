#!/usr/bin/env bash
# Build dist/vortax_<version>_all.deb with plain dpkg-deb (no debhelper or fakeroot needed).
#   MAINTAINER="Name <mail>"  override the Maintainer field
#   HOMEPAGE=https://...      override the Homepage field
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

pyver="$(python3 -c "import re,pathlib;print(re.search(r'__version__ = \"(.+?)\"',pathlib.Path('src/vortax/__init__.py').read_text()).group(1))")"
# PEP 440 "3.0.0b1" -> Debian "3.0.0~b1" so betas sort before the final release.
debver="$(printf '%s' "$pyver" | sed -E 's/(a|b|rc)([0-9]+)$/~\1\2/')-1"
maint="${MAINTAINER:-Vortax Maintainers <maintainers@example.invalid>}"
home="${HOMEPAGE:-https://github.com/CodeVortax/vortax}"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
pkg="$stage/pkg"
lib="$pkg/usr/lib/python3/dist-packages"

install -d "$lib" "$pkg/usr/bin" "$pkg/DEBIAN" \
    "$pkg/usr/share/doc/vortax" "$pkg/usr/share/man/man1" \
    "$pkg/usr/share/bash-completion/completions" "$pkg/usr/lib/systemd/user"

cp -r src/vortax "$lib/vortax"
find "$lib" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$lib" -type d -exec chmod 755 {} +
find "$lib" -type f -exec chmod 644 {} +

cat > "$pkg/usr/bin/vortax" <<'PY'
#!/usr/bin/python3
import sys
from vortax.cli import main
sys.exit(main())
PY
chmod 755 "$pkg/usr/bin/vortax"

gzip -9n -c packaging/vortax.1 > "$pkg/usr/share/man/man1/vortax.1.gz"
install -m 644 packaging/vortax.bash-completion "$pkg/usr/share/bash-completion/completions/vortax"
install -m 644 packaging/vortax-dashboard.service "$pkg/usr/lib/systemd/user/vortax-dashboard.service"
install -m 644 README.md "$pkg/usr/share/doc/vortax/README.md"
sed "s#https://github.com/CodeVortax/vortax#$home#" debian/copyright > "$pkg/usr/share/doc/vortax/copyright"
chmod 644 "$pkg/usr/share/doc/vortax/copyright"
grep -q "GNU AFFERO GENERAL PUBLIC LICENSE" LICENSE || { echo "LICENSE is not the AGPL text" >&2; exit 1; }
install -m 644 LICENSE "$pkg/usr/share/doc/vortax/LICENSE"
{
  printf 'vortax (%s) unstable; urgency=low\n\n' "$debver"
  sed -e 's/^/  /' CHANGELOG.md | head -n 40
  printf '\n -- %s  %s\n' "$maint" "$(date -R)"
} | gzip -9n > "$pkg/usr/share/doc/vortax/changelog.gz"
chmod 644 "$pkg/usr/share/doc/vortax/changelog.gz"

size="$(du -sk --exclude=DEBIAN "$pkg" | cut -f1)"
sed -e "s/@VERSION@/$debver/" -e "s/@SIZE@/$size/" -e "s#@MAINTAINER@#$maint#" \
    -e "s#https://github.com/CodeVortax/vortax#$home#" debian/control > "$pkg/DEBIAN/control"
install -m 755 debian/postinst debian/prerm "$pkg/DEBIAN/"

mkdir -p dist
out="dist/vortax_${debver}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$pkg" "$out" >/dev/null
echo "$out"
