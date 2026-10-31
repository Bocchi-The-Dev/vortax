# Installing from the apt repository

Releases are also published as a signed apt repository on GitHub Pages, so `apt upgrade` keeps Vortax current.

```sh
curl -fsSL https://codevortax.github.io/vortax/vortax.asc | sudo tee /usr/share/keyrings/vortax.asc >/dev/null
echo "deb [signed-by=/usr/share/keyrings/vortax.asc] https://codevortax.github.io/vortax stable main" \
  | sudo tee /etc/apt/sources.list.d/vortax.list
sudo apt update && sudo apt install vortax
```

## One-time setup for the maintainer

1. Create a signing key (a dedicated one, no expiry or a long one):
   `gpg --quick-gen-key "Vortax Releases <you@example.com>" ed25519 sign never`
2. Export it and store it as a repository secret named `APT_GPG_PRIVATE_KEY`:
   `gpg --armor --export-secret-keys <fingerprint>`
   If the key has a passphrase, also add `APT_GPG_PASSPHRASE`.
3. Repository Settings, Pages, Source: **GitHub Actions**.
4. Push a tag (`git tag v3.1.0b1 && git push origin v3.1.0b1`). The `Release` workflow builds the `.deb`, publishes the
   GitHub release and, when the secret exists, deploys the repository.

Without the secret the workflow still publishes the release and skips the apt job.

## Build the repository by hand

```sh
packaging/build-deb.sh
GPG_KEY_ID=<fingerprint> packaging/make-apt-repo.sh dist apt
# upload ./apt to any static web host
```
