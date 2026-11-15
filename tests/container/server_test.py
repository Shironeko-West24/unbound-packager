import os
import re
import shutil
import subprocess

from ubp import containers
from ubp.util import UbpError, log

TOOL = "/usr/local/sbin/unbound-update"
CONF = "/etc/unbound-packager/unbound-update.conf"


class Box:
    def __init__(self, name):
        self.name = name
        self.results = []

    def sh(self, *cmd, data=None):
        proc = subprocess.run(containers.engine() + ["exec", "-i", self.name, *cmd], input=data, capture_output=True)
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")

    def tool(self, *args):
        return self.sh("python3", "-I", TOOL, *args)

    def write(self, path, text):
        self.sh("sh", "-c", f"mkdir -p \"$(dirname {path})\" && cat > {path}", data=text.encode("utf-8"))

    def record(self, name, ok, detail=""):
        self.results.append((name, bool(ok), detail))
        log(f"  {'OK' if ok else 'NG'} {name}" + ("" if ok else "\n     " + detail[-2000:].strip().replace("\n", "\n     ")))


def os_versions_pinned_away(policy):
    table = policy.split("Version table:", 1)[1] if "Version table:" in policy else ""
    rows = re.findall(r"(?m)^\s*(?:\*\*\*)?\s*(\S+)\s+(-?\d+)\s*$", table)
    others = [(version, int(priority)) for version, priority in rows if "+local" not in version]
    return bool(others) and all(priority == -1 for _, priority in others)


def config_text(port, checks, local=""):
    return (
        "[source]\nrepository = example/unbound-packager\n"
        f"base_url = http://127.0.0.1:{port}\n\n[checks]\n"
        f"resolve = {' '.join(checks.get('resolve', []))}\n"
        f"dnssec_valid = {checks.get('dnssec_valid', '')}\n"
        f"dnssec_bogus = {checks.get('dnssec_bogus', '')}\n"
        f"local = {local}\nstable_seconds = 8\n"
    )


def stage_release(work, os_name, tag):
    source = os.path.join(str(work), "release")
    target = os.path.join(str(work), "servertest", os_name, "release")
    shutil.rmtree(target, ignore_errors=True)
    for sub in (os.path.join("download", tag), os.path.join("latest", "download")):
        shutil.copytree(source, os.path.join(target, sub))
    return target


def run(code, work, os_name, tag, checks):
    staged = stage_release(work, os_name, tag)
    src = os.path.join(str(work), "servertest", os_name, "src")
    containers.prepare_source(code, src)
    name = f"ubp-server-{os_name}"
    containers.start_systemd(code, f"test-{os_name}", name, [(staged, "/release", "ro,Z"), (src, "/ubp", "ro,Z")])
    box = Box(name)
    try:
        exercise(box, os_name, tag, checks)
    finally:
        containers.remove(name)
        shutil.rmtree(os.path.dirname(staged), ignore_errors=True)
    failed = [item for item in box.results if not item[1]]
    if failed:
        total = len(box.results)
        raise UbpError(f"{os_name}: unbound-update の実地テスト {total} 項目中 {total - len(failed)} 項目 OK、{len(failed)} 項目 NG\n" + "\n".join(f"  NG {n}: {d[-400:]}" for n, _, d in failed))
    log(f"{os_name}: unbound-update の実地テスト {len(box.results)} 項目すべて OK")
    return box.results


def exercise(box, os_name, tag, checks):
    box.sh("install", "-D", "-m", "755", "/ubp/server/unbound-update", TOOL)
    subprocess.run(containers.engine() + ["exec", "-d", box.name, "python3", "-m", "http.server", "8000", "--bind", "127.0.0.1", "--directory", "/release"], capture_output=True)
    box.sh("sleep", "2")
    rc, out = box.tool("init", "--repository", "example/unbound-packager")
    box.record("設定ファイルの作成", rc == 0, out)
    box.write(CONF, config_text(8000, checks))
    rc, out = box.tool("check")
    box.record("公開されている版の確認", rc == 0 and tag in out, out)
    rc, out = box.tool("update")
    box.record("初回は update を拒否して migrate を案内する", rc == 2 and "migrate" in out, out)
    rc, out = box.tool("migrate")
    box.record("初回の導入（設定なし）", rc == 0 and "移行と確認が終わりました" in out, out)
    if os_name == "rocky10":
        rc, out = box.sh("dnf", "-q", "repoquery", "unbound", "unbound-libs")
        box.record("OS 標準 unbound の除外（dnf）", rc == 0 and not out.strip(), out)
        rc, out = box.sh("dnf", "-q", "--disableexcludes=main", "repoquery", "unbound")
        box.record("除外を外すと OS 標準が見える", rc == 0 and "unbound" in out, out)
    else:
        box.sh("apt-get", "update")
        rc, out = box.sh("apt-cache", "policy", "unbound")
        box.record("OS 標準 unbound の除外（APT の優先度 -1）", rc == 0 and os_versions_pinned_away(out), out)
        rc, out = box.sh("apt-config", "dump", "Unattended-Upgrade::Package-Blacklist")
        box.record("unattended-upgrades の除外", rc == 0 and "unbound" in out, out)
    old = "/usr/local/etc/unbound"
    box.write(f"{old}/unbound.conf", f'server:\n    interface: 127.0.0.1\n    do-ip6: no\n    access-control: 127.0.0.0/8 allow\n    include: "{old}/extra.conf"\n')
    box.write(f"{old}/extra.conf", 'local-zone: "ubp-test.example." static\nlocal-data: "ubp-test.example. A 192.0.2.53"\n')
    rc, out = box.tool("save", "--output", "/tmp/old.tar", "--config-dir", old)
    box.record("旧設定の保存", rc == 0, out)
    local_only = {"resolve": [], "dnssec_valid": "", "dnssec_bogus": ""}
    box.write(CONF, config_text(8000, local_only, "ubp-test.example"))
    rc, out = box.tool("migrate", "--config", "/tmp/old.tar")
    box.record("設定の取り込みとパスの置き換え", rc == 0 and "パスを置き換えました" in out, out)
    rc, out = box.tool("verify")
    box.record("取り込んだ設定での確認", rc == 0, out)
    rc, out = box.tool("update", "--force")
    box.record("入れ替え（同じ版の入れ直し）", rc == 0 and "更新と確認が終わりました" in out, out)
    box.write(CONF, config_text(8000, local_only, "no-such-name.ubp-test.invalid"))
    rc, out = box.tool("update", "--force")
    box.record("確認に失敗したときの自動の切り戻し", rc == 1 and "切り戻しました" in out, out)
    box.write(CONF, config_text(8000, local_only, "ubp-test.example"))
    rc, out = box.tool("verify")
    box.record("切り戻し後の動作", rc == 0, out)
    box.sh("sh", "-c", f"cp -r /release /tmp/bad && for f in /tmp/bad/download/{tag}/*.rpm /tmp/bad/download/{tag}/*.deb; do [ -f \"$f\" ] && echo x >> \"$f\"; done; true")
    subprocess.run(containers.engine() + ["exec", "-d", box.name, "python3", "-m", "http.server", "8001", "--bind", "127.0.0.1", "--directory", "/tmp/bad"], capture_output=True)
    box.sh("sleep", "2")
    box.write(CONF, config_text(8001, local_only, "ubp-test.example"))
    rc, out = box.tool("update", "--force", "--tag", tag)
    box.record("改ざんされたパッケージの拒否", rc != 0 and "SHA-256" in out, out)
    box.write(CONF, config_text(8000, local_only, "ubp-test.example"))
    rc, out = box.tool("verify")
    box.record("拒否のあとも動作している", rc == 0, out)
    rc, out = box.tool("rollback")
    box.record("切り戻し先がないときは何もせずに止まる", rc == 2 and "切り戻し先" in out, out)
    rc, out = box.tool("self-update", "--target", "/tmp/unbound-update.new")
    rc2, _ = box.sh("cmp", "/tmp/unbound-update.new", "/ubp/server/unbound-update")
    box.record("コマンド自体の更新", rc == 0 and rc2 == 0, out)
    rc, out = box.tool("uninstall")
    box.record("解除", rc == 0, out)
    if os_name == "rocky10":
        rc, out = box.sh("cat", "/etc/dnf/dnf.conf")
        box.record("解除後に除外設定が残っていない", "unbound" not in out, out)
    else:
        rc, out = box.sh("ls", "/etc/apt/preferences.d/")
        box.record("解除後に除外設定が残っていない", "unbound-packager" not in out, out)
