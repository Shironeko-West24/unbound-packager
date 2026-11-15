import atexit
import functools
import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from ubp import release, signing, util

TOOL = ROOT / "server" / "unbound-update"
FAKE_NAMES = ["podman", "dnf", "rpm", "apt-get", "dpkg-query", "systemctl", "unbound-checkconf", "unbound-control", "firewall-cmd", "ufw", "gh"]
PKG = {"rocky10": "{v}-{r}.local.el10_2", "ubuntu2604": "{v}-0+local{r}"}
FILES = {
    "rocky10": ["unbound-{p}.x86_64.rpm", "unbound-libs-{p}.x86_64.rpm", "unbound-devel-{p}.x86_64.rpm"],
    "ubuntu2604": ["unbound_{p}_amd64.deb", "libunbound8_{p}_amd64.deb", "libunbound-dev_{p}_amd64.deb"],
}
OS_RELEASE = {"rocky10": 'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="10.1"\n', "ubuntu2604": 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="26.04"\n'}


_SIGNING = {}


def signing_key():
    if not _SIGNING:
        directory = tempfile.mkdtemp(prefix="ubp-test-key-")
        atexit.register(shutil.rmtree, directory, True)
        key_file, public = signing.generate(directory, "unbound-packager test")
        _SIGNING.update(file=key_file, public=public)
    return _SIGNING


def sign_release(target):
    return signing.sign(signing_key()["file"], os.path.join(str(target), "SHA256SUMS"))


class FakeDns(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.fail = set()
        self.fail_times = {}
        self.asked = []

    def run(self):
        while True:
            try:
                data, peer = self.sock.recvfrom(4096)
            except OSError:
                return
            labels = []
            at = 12
            while data[at]:
                labels.append(data[at + 1:at + 1 + data[at]].decode())
                at += data[at] + 1
            name = ".".join(labels)
            self.asked.append(name)
            transient = self.fail_times.get(name, 0) > 0
            if transient:
                self.fail_times[name] -= 1
            flags, answers = (0x8182, 0) if name.startswith("bogus") or name in self.fail or transient else (0x81A0, 1)
            self.sock.sendto(data[:2] + struct.pack(">HHHHH", flags, 1, answers, 0, 0) + data[12:], peer)

    def close(self):
        self.sock.close()


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def make_out(work, version, revision, os_name, tested=True):
    pkg = PKG[os_name].format(v=version, r=revision)
    directory = Path(work) / "out" / os_name
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    files = []
    for template in FILES[os_name]:
        fname = template.format(p=pkg)
        content = f"package {fname}".encode()
        (directory / fname).write_bytes(content)
        name = fname.split("_")[0] if fname.endswith(".deb") else fname[: fname.index("-" + pkg)]
        files.append({"name": name, "file": fname, "sha256": hashlib.sha256(content).hexdigest()})
    source = f"unbound-{pkg}.src.rpm" if os_name == "rocky10" else f"unbound_{pkg}.dsc"
    (directory / source).write_bytes(b"source")
    manifest = {
        "os": os_name, "upstream": version, "pkg_version": pkg, "files": files,
        "all_packages": [f["name"] for f in files],
        "patches": [{"name": "a.patch", "action": "drop", "reason": "上流に取り込み済み"}],
        "unpackaged": ["/usr/share/new"] if os_name == "rocky10" else [],
        "sources": [{"file": source, "sha256": hashlib.sha256(b"source").hexdigest()}],
        "licenses": ["BSD-3-Clause"], "distro_source": "1.24.2-7.el10_2.6",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if tested:
        (directory / "test-report.json").write_text(json.dumps({"ok": True, "results": []}), encoding="utf-8")
    return manifest


def make_tar(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, content in entries:
            data = content if isinstance(content, bytes) else content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class Sandbox(unittest.TestCase):
    os_name = "rocky10"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        (self.root / "etc" / "unbound").mkdir(parents=True)
        (self.root / "etc" / "os-release").write_text(OS_RELEASE[self.os_name])
        (self.root / "etc" / "unbound" / "unbound.conf").write_text("server:\n    verbosity: 1\n")
        if self.os_name == "rocky10":
            (self.root / "etc" / "dnf").mkdir()
            (self.root / "etc" / "dnf" / "dnf.conf").write_text("[main]\ngpgcheck=1\nexcludepkgs=kernel*\n\n[other]\nexclude=foo\n")
        self.fake = self.base / "fake"
        self.fake.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name in FAKE_NAMES:
            os.symlink(ROOT / "tests" / "fakes" / "fakecmd.py", self.bin / name)
        self.site = self.base / "site"
        self.site.mkdir()
        self.dns = FakeDns()
        self.dns.start()
        handler = functools.partial(Quiet, directory=str(self.site))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.env = dict(os.environ)
        self.env.update({
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "UBP_FAKE_DIR": str(self.fake),
            "UBP_TEST_ROOT": str(self.root),
            "UBP_TEST_DNS": f"127.0.0.1:{self.dns.port}",
            "UBP_TEST_SIGNING_KEY": signing_key()["public"],
        })
        self.write_config()
        util.SECRETS.clear()
        util.LOG.echo = False

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dns.close()
        util.LOG.echo = True
        self.tmp.cleanup()

    def write_config(self, extra="", stable=0, local=""):
        path = self.root / "etc" / "unbound-packager" / "unbound-update.conf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"[source]\nrepository = owner/unbound-packager\nbase_url = {self.url}\n\n"
            f"[checks]\nresolve = example.com\ndnssec_valid = signed.example\ndnssec_bogus = bogus.example\n"
            f"local = {local}\nstable_seconds = {stable}\n{extra}",
            encoding="utf-8",
        )

    def tool(self, *args, stdin=None):
        proc = subprocess.run([sys.executable, "-I", str(TOOL), *args], capture_output=True, text=True, env=self.env, input=stdin)
        return proc.returncode, proc.stdout + proc.stderr

    def publish(self, version, revision=1, latest=True):
        work = self.base / f"work-{version}-{revision}"
        for os_name in ("rocky10", "ubuntu2604"):
            make_out(work, version, revision, os_name)
        target, combined = release.assemble(ROOT, work, version, revision)
        sign_release(target)
        tag = combined["tag"]
        shutil.copytree(target, self.site / "download" / tag)
        if latest:
            shutil.rmtree(self.site / "latest", ignore_errors=True)
            shutil.copytree(target, self.site / "latest" / "download")
        return tag

    def rules(self, rules):
        (self.fake / "rules.json").write_text(json.dumps(rules), encoding="utf-8")

    def calls(self, cmd=None):
        path = self.fake / "calls.jsonl"
        if not path.exists():
            return []
        items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [c for c in items if cmd is None or c["cmd"] == cmd]

    def clear_calls(self):
        path = self.fake / "calls.jsonl"
        if path.exists():
            path.unlink()

    def installed(self):
        path = self.root / "fake-installed"
        return path.read_text() if path.exists() else ""

    def state(self):
        return json.loads((self.root / "var/lib/unbound-packager/state.json").read_text())

    def pkg(self, version, revision=1):
        return PKG[self.os_name].format(v=version, r=revision)

    def seed(self, old="1.26.1", new="1.26.2"):
        self.publish(old)
        rc, out = self.tool("migrate")
        self.assertEqual(rc, 0, out)
        self.publish(new)
        self.clear_calls()
