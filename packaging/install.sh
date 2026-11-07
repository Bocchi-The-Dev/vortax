#!/bin/sh
# One-command installer for the Vortax apt repository (Debian, Ubuntu and derivatives).
#
#   curl -fsSL https://codevortax.github.io/vortax/install.sh | sudo sh
#
# It does exactly four things, and you can read them below:
#   1. downloads the repository signing key to /usr/share/keyrings/vortax.asc
#   2. writes /etc/apt/sources.list.d/vortax.list (restricted to that key with signed-by)
#   3. runs apt-get update
#   4. runs apt-get install vortax
# To undo it: sudo apt-get remove vortax && sudo rm /etc/apt/sources.list.d/vortax.list /usr/share/keyrings/vortax.asc
#
# The body is one function that runs on the last line, so a download cut off halfway does nothing.
set -eu

main() {
    base="${VORTAX_REPO_URL:-https://codevortax.github.io/vortax}"

    if [ -d /data/data/com.termux ]; then
        echo "This is Termux: it does not use this apt repository. Install with pip instead:" >&2
        echo "    pip install git+https://github.com/CodeVortax/vortax" >&2
        exit 1
    fi
    if [ "$(id -u)" -ne 0 ]; then
        echo "Run this as root:  curl -fsSL $base/install.sh | sudo sh" >&2
        exit 1
    fi
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "apt-get was not found. This installer is for Debian, Ubuntu and derivatives." >&2
        echo "Anywhere else with Python 3.9+:  pipx install git+https://github.com/CodeVortax/vortax" >&2
        exit 1
    fi
    if ! command -v curl >/dev/null 2>&1; then
        apt-get update -qq </dev/null
        apt-get install -y curl ca-certificates </dev/null
    fi

    install -d -m 755 /usr/share/keyrings
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT
    curl -fsSL "$base/vortax.asc" -o "$tmp"
    grep -q "BEGIN PGP PUBLIC KEY BLOCK" "$tmp" || { echo "the downloaded key does not look like a PGP key" >&2; exit 1; }
    install -m 644 "$tmp" /usr/share/keyrings/vortax.asc

    echo "deb [signed-by=/usr/share/keyrings/vortax.asc] $base stable main" > /etc/apt/sources.list.d/vortax.list

    # </dev/null: when piped from curl, stdin is this script, so apt must not read it
    apt-get update </dev/null
    apt-get install -y vortax </dev/null
    echo
    echo "Installed. Try:  vortax doctor"
}

main "$@"
