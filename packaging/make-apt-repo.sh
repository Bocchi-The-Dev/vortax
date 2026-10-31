#!/bin/bash
# Build a signed, static apt repository from dist/*.deb into ./apt (host it on GitHub Pages or any web server).
#   GPG_KEY_ID=<fingerprint> packaging/make-apt-repo.sh [debs-dir] [out-dir]
# The key must already be in the gpg keyring (the release workflow imports it from a secret).
set -euo pipefail
cd "$(dirname "$0")/.."
debs="${1:-dist}"
out="${2:-apt}"
: "${GPG_KEY_ID:?set GPG_KEY_ID to the signing key fingerprint}"
command -v dpkg-scanpackages >/dev/null || { echo "need dpkg-dev (dpkg-scanpackages)" >&2; exit 1; }
compgen -G "$debs/*.deb" >/dev/null || { echo "no .deb files in $debs" >&2; exit 1; }

rm -rf "$out"
pool="$out/pool/main/v/vortax"
dist="$out/dists/stable/main/binary-all"
mkdir -p "$pool" "$dist"
cp "$debs"/*.deb "$pool/"

( cd "$out" && dpkg-scanpackages --multiversion pool/main > "dists/stable/main/binary-all/Packages" 2>/dev/null )
gzip -9nk "$dist/Packages"
xz -9k "$dist/Packages"

rel="$out/dists/stable/Release"
{
  echo "Origin: Vortax"
  echo "Label: Vortax"
  echo "Suite: stable"
  echo "Codename: stable"
  echo "Architectures: all amd64 arm64"
  echo "Components: main"
  echo "Date: $(LC_ALL=C date -Ru)"
  echo "SHA256:"
  ( cd "$out/dists/stable" && find main -type f | sort | while read -r f; do
      printf ' %s %16d %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$(stat -c %s "$f")" "$f"
    done )
} > "$rel"

gpg --batch --yes --pinentry-mode loopback ${GPG_PASSPHRASE:+--passphrase "$GPG_PASSPHRASE"} \
    --default-key "$GPG_KEY_ID" --clearsign -o "$out/dists/stable/InRelease" "$rel"
gpg --batch --yes --pinentry-mode loopback ${GPG_PASSPHRASE:+--passphrase "$GPG_PASSPHRASE"} \
    --default-key "$GPG_KEY_ID" --armor --detach-sign -o "$out/dists/stable/Release.gpg" "$rel"
gpg --batch --yes --export "$GPG_KEY_ID" > "$out/vortax.gpg"
gpg --batch --yes --armor --export "$GPG_KEY_ID" > "$out/vortax.asc"
touch "$out/.nojekyll"
cat > "$out/index.html" <<'HTML'
<!doctype html><meta charset="utf-8"><title>Vortax apt repository</title>
<h1>Vortax apt repository</h1>
<pre>
curl -fsSL URL/vortax.asc | sudo tee /usr/share/keyrings/vortax.asc &gt;/dev/null
echo "deb [signed-by=/usr/share/keyrings/vortax.asc] URL stable main" | sudo tee /etc/apt/sources.list.d/vortax.list
sudo apt update &amp;&amp; sudo apt install vortax
</pre>
HTML
echo "repository written to $out"
