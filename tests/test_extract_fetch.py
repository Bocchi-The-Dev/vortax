import base64
import gzip
import io
import os
import tarfile
import unittest
import zipfile
from pathlib import Path

from vortax import extract, fetch, tunnels
from vortax.errors import VortaxError
from tests.helpers import Sandbox

QCOW = b"QFI\xfb" + b"\0" * 600


class Sniff(Sandbox):
    def w(self, name, data):
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def test_gzip_is_not_mistaken_for_zip(self):
        # the original workflow's bug: "application/gzip" contains the substring "zip"
        p = self.w("a.img.gz", gzip.compress(b"raw disk bytes" * 100))
        self.assertEqual(extract.sniff(p), "gzip")

    def test_kinds(self):
        self.assertEqual(extract.sniff(self.w("q", QCOW)), "qcow2")
        self.assertEqual(extract.sniff(self.w("z", b"PK\x03\x04" + b"\0" * 40)), "zip")
        self.assertEqual(extract.sniff(self.w("s", b"7z\xbc\xaf\x27\x1c" + b"\0" * 40)), "7z")
        iso = bytearray(40000); iso[0x8001:0x8006] = b"CD001"
        self.assertEqual(extract.sniff(self.w("i", bytes(iso))), "iso")
        self.assertEqual(extract.sniff(self.w("u", b"hello")), "unknown")

    def test_plain_gz_is_decompressed_not_untarred(self):
        src = self.w("batocera.img.gz", gzip.compress(b"\x00" * 2048))
        out = self.tmp / "out"
        extract.extract(src, out)
        self.assertEqual([p.name for p in out.iterdir()], ["batocera.img"])
        self.assertEqual((out / "batocera.img").stat().st_size, 2048)

    def test_tar_gz_is_untarred(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo("sub/disk.qcow2"); info.size = len(QCOW)
            tf.addfile(info, io.BytesIO(QCOW))
        src = self.w("d.tar.gz", buf.getvalue())
        out = self.tmp / "o"
        extract.extract(src, out)
        self.assertTrue((out / "sub" / "disk.qcow2").exists())

    def test_zip_slip_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../evil.txt", "x")
        src = self.w("evil.zip", buf.getvalue())
        with self.assertRaisesRegex(VortaxError, "outside"):
            extract.extract(src, self.tmp / "o")
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_tar_symlink_is_refused(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("link"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"
            tf.addfile(info)
        src = self.w("l.tar", buf.getvalue())
        with self.assertRaisesRegex(VortaxError, "link"):
            extract.extract(src, self.tmp / "o")

    def test_find_media_picks_largest_disk_and_detects_real_format(self):
        d = self.tmp / "w"; d.mkdir()
        (d / "small.img").write_bytes(b"\0" * 10)
        (d / "big.img").write_bytes(b"\0" * 5000)          # extension says img, content is raw
        m = extract.find_media(d)
        self.assertEqual((m.kind, m.path.name, m.disk_format), ("disk", "big.img", "raw"))
        (d / "big.img").write_bytes(QCOW)                   # now it really is qcow2
        self.assertEqual(extract.find_media(d).disk_format, "qcow2")

    def test_find_media_handles_a_nested_archive(self):
        d = self.tmp / "n"; d.mkdir()
        (d / "inner.img.gz").write_bytes(gzip.compress(QCOW))
        m = extract.find_media(d)
        self.assertEqual(m.disk_format, "qcow2")

    def test_nothing_usable_is_an_error(self):
        d = self.tmp / "e"; d.mkdir(); (d / "readme.txt").write_text("hi")
        with self.assertRaises(VortaxError):
            extract.find_media(d)


class Fetch(Sandbox):
    def test_mediafire_scrambled_and_direct(self):
        link = "https://download1234.mediafire.com/abc/file.7z"
        scrambled = f'<a data-scrambled-url="{base64.b64encode(link.encode()).decode()}">'
        self.assertEqual(fetch.parse_mediafire(scrambled), link)
        self.assertEqual(fetch.parse_mediafire(f'<a href="{link}">x</a>'), link)
        with self.assertRaises(VortaxError):
            fetch.parse_mediafire("<html>gone</html>")

    def test_cache_name_is_stable_and_safe(self):
        a = fetch.cache_name("https://x.test/a b/Ubuntu 25.10.iso?x=1")
        self.assertEqual(a, fetch.cache_name("https://x.test/a b/Ubuntu 25.10.iso?x=1"))
        self.assertRegex(a, r"^[0-9a-f]{12}-[A-Za-z0-9._-]+$")

    def test_command_chain_order_and_resume_flags(self):
        cmds = fetch.candidate_commands("https://x.test/f", Path("/c/f"), {"aria2c": "/a", "curl": "/c", "wget": "/w"})
        self.assertEqual([c[0] for c in cmds], ["aria2c", "curl", "wget"])
        self.assertIn("--continue=true", cmds[0][1])
        self.assertIn("-C", cmds[1][1])
        self.assertIn("-c", cmds[2][1])
        self.assertEqual(fetch.candidate_commands("u", Path("/c/f"), {}), [])

    def test_local_sources(self):
        f = self.tmp / "x.iso"; f.write_bytes(b"1")
        self.assertTrue(fetch.is_local(str(f)))
        self.assertEqual(fetch.download(str(f), self.tmp / "cache"), f)
        self.assertEqual(fetch.download(f.as_uri(), self.tmp / "cache"), f)
        with self.assertRaises(VortaxError):
            fetch.download("/nope/missing.iso", self.tmp / "cache")
        with self.assertRaises(VortaxError):
            fetch.download("ftp://x.test/a", self.tmp / "cache")

    def test_sha256_mismatch_deletes_the_file(self):
        # serve nothing: use a pre-seeded cache entry to exercise the checksum path via a fake downloader
        url = "https://x.test/file.bin"
        dest = self.tmp / "cache" / fetch.cache_name(url)
        dest.parent.mkdir()
        orig, orig_resolve = fetch.candidate_commands, fetch.resolve_url
        fetch.candidate_commands = lambda u, d, have=None: [("fake", ["python3", "-c", f"open({str(d)!r},'wb').write(b'abc')"])]
        fetch.resolve_url = lambda u: u
        try:
            with self.assertRaisesRegex(VortaxError, "checksum mismatch"):
                fetch.download(url, self.tmp / "cache", sha256="0" * 64, log=lambda m: None)
            self.assertFalse(dest.exists())
            import hashlib
            self.assertEqual(fetch.download(url, self.tmp / "cache", sha256=hashlib.sha256(b"abc").hexdigest(), log=lambda m: None), dest)
        finally:
            fetch.candidate_commands, fetch.resolve_url = orig, orig_resolve


class TunnelParsers(unittest.TestCase):
    def test_bore(self):
        log = "2026 INFO connected to server\n2026 INFO listening at bore.pub:41234\n"
        self.assertEqual(tunnels.parse_bore(log), "bore.pub:41234")
        self.assertEqual(tunnels.parse_bore("connecting..."), "")

    def test_ngrok(self):
        js = '{"tunnels":[{"public_url":"tcp://0.tcp.eu.ngrok.io:12345","config":{"addr":"localhost:5900"}},' \
             '{"public_url":"https://x.ngrok.app","config":{"addr":"localhost:80"}}]}'
        self.assertEqual(tunnels.parse_ngrok(js, 5900), "0.tcp.eu.ngrok.io:12345")
        self.assertEqual(tunnels.parse_ngrok(js, 5901), "")
        self.assertEqual(tunnels.parse_ngrok("not json", 5900), "")


if __name__ == "__main__":
    unittest.main()
