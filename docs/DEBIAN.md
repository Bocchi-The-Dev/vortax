# Getting Vortax into the official Debian archive

Goal: `sudo apt install vortax` on a stock Debian or Ubuntu, with no extra repository. This is a review process run
by volunteers; expect months, and expect the package to reach a *stable* release only with the next release.

What is already done: `debian/` is a real Debian source package (debhelper 13, `pybuild`, DEP-5 copyright with the
full licence text, `3.0 (quilt)`, a `watch` file, an autopkgtest). The workflow *Debian packaging* builds it with
`dpkg-buildpackage` on Debian stable, Debian unstable and Ubuntu 24.04, runs `lintian`, then installs and runs the
result. The older `packaging/build-deb.sh` (plain `dpkg-deb`, templates in `packaging/deb/`) still produces the
`.deb` that releases and the GitHub Pages apt repository use.

## Before the first upload

1. Pick the real maintainer: replace `Vortax Maintainers <maintainers@example.invalid>` in `debian/control` and
   `debian/changelog` with a name and an address you read. Debian needs a real contact.
2. Check the name is free at https://tracker.debian.org/pkg/vortax and `rmadison vortax`.
3. Make lintian clean (see the workflow log; fix warnings, not only errors).
4. Decide how to answer "does this belong in main?": the catalog downloads installers of proprietary systems and
   `tunnel` can use ngrok. Reviewers may ask; be ready to explain that nothing non-free is shipped or required.

## Steps

1. File an Intent To Package bug: `reportbug wnpp` (choose ITP). Put its number in `debian/changelog`
   (replace `FIXME-ITP-BUG-NUMBER`).
2. Build with `dpkg-buildpackage -S` and upload to https://mentors.debian.net.
3. Ask for a sponsor: the debian-mentors list, or a "request for sponsorship" bug. A Debian Developer reviews and
   uploads for you.
4. The upload waits in the NEW queue for the ftp-masters (licences and copyright are checked).
5. It enters `unstable`, migrates to `testing` after a few days if nothing breaks, and ships in the next stable
   release. Ubuntu mostly imports it from Debian.

An Ubuntu PPA needs the same source package but no human review: `debuild -S` then `dput ppa:<you>/vortax ...`.
