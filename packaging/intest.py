#!/usr/bin/python3 -I
import argparse
import base64
import glob
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
from ubp import pkgedit

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail[-800:]})
    print(f"{'OK' if ok else 'NG'} {name}" + ("" if ok else "\n   " + detail[-300:].strip().replace("\n", "\n   ")), flush=True)


def sh(cmd, env=None):
    proc = subprocess.run(cmd, capture_output=True, env=dict(os.environ, **(env or {})))
    return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")


def package_files(directory):
    rpms = sorted(path for path in glob.glob(os.path.join(directory, "*.rpm")) if not path.endswith(".src.rpm"))
    debs = sorted(glob.glob(os.path.join(directory, "*.deb")))
    return rpms, debs


def diagnose(names):
    lines = []
    anchors = [path for path in ("/var/lib/unbound/root.key", "/usr/share/dns/root.key", "/etc/unbound/root.key") if os.path.exists(path)]
    lines.append("トラストアンカーの候補: " + (" ".join(anchors) or "なし"))
    for command in (
        ["unbound-control", "get_option", "module-config"],
        ["unbound-control", "get_option", "auto-trust-anchor-file"],
        ["unbound-control", "get_option", "trust-anchor-file"],
        ["unbound-control", "set_option", "val-log-level:", "2"],
        ["unbound-control", "flush_bogus"],
        ["unbound-control", "flush_infra", "all"],
    ):
        rc, out = sh(command)
        lines.append(f"$ {' '.join(command)} → {rc}: {out.strip()[-300:]}")
    for name in names:
        rc, out = sh(["unbound-control", "lookup", name])
        lines.append(f"$ unbound-control lookup {name} → {rc}:\n{out.strip()[-1500:]}")
        for anchor in anchors[:1]:
            rc, out = sh(["unbound-host", "-v", "-d", "-d", "-f", anchor, "-t", "A", name])
            interesting = [line for line in out.splitlines() if any(word in line for word in ("validat", "bogus", "secure", "insecure", "verify", "algorithm", "error", "fail", "has address", "no address"))]
            lines.append(f"$ unbound-host -v -d -d -f {anchor} -t A {name} → {rc}:\n" + "\n".join(interesting[-60:]))
    return "\n".join(lines)


def load_agent():
    path = os.path.join(HERE, "..", "server", "unbound-update")
    loader = importlib.machinery.SourceFileLoader("ubp_agent", path)
    spec = importlib.util.spec_from_loader("ubp_agent", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checks", default="")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    opts = json.load(open(os.path.join(HERE, "options.json"), encoding="utf-8"))
    checks = json.loads(base64.urlsafe_b64decode(args.checks.encode()).decode()) if args.checks else {}
    rpms, debs = package_files("/pkgs")
    if rpms:
        rc, out = sh(["dnf", "-y", "install", *rpms])
    else:
        sh(["apt-get", "update"])
        rc, out = sh(["apt-get", "-y", "install", *debs], env={"DEBIAN_FRONTEND": "noninteractive"})
    record("全サブパッケージの導入", rc == 0, out)
    if rc == 0:
        binary = shutil.which("unbound") or "/usr/sbin/unbound"
        rc, out = sh([binary, "-V"])
        info = pkgedit.parse_unbound_version_output(out)
        missing = pkgedit.check_configure_line(info["configure"], opts["configure"])
        record("統一したビルドオプション", rc == 0 and not missing, "足りないオプション: " + " ".join(missing))
        record("リンクされたモジュール", info["modules"] == opts["expected_modules"], f"実際: {info['modules']}")
        rc, out = sh(["readelf", "-h", binary])
        record("PIE", rc == 0 and "DYN" in out, out[-200:])
        rc, out = sh(["readelf", "-d", binary])
        record("RELRO NOW", rc == 0 and ("BIND_NOW" in out or "NOW" in out), out[-200:])
        rc, out = sh(["ldd", binary])
        absent = [lib for lib in opts["linked_libraries"] if lib not in out]
        record("OS 標準ライブラリへのリンク", rc == 0 and not absent, "見つからないライブラリ: " + " ".join(absent))
        found = [path for pattern in opts["license_files"] for path in glob.glob(pattern)]
        record("ライセンス文書の同梱", bool(found), "見つかりません: " + " ".join(opts["license_files"]))
        for tool in ("unbound-anchor", "unbound-host", "unbound-control", "unbound-checkconf"):
            record(f"コマンド {tool}", shutil.which(tool) is not None)
        rc, out = sh(["python3", "-c", "import unbound; unbound.ub_ctx()"])
        record("Python バインディング", rc == 0, out)
        rc, out = sh(["unbound-checkconf"])
        record("unbound-checkconf", rc == 0, out)
        wrong = []
        for name, value in opts.get("server_options", {}).items():
            rc, out = sh(["unbound-checkconf", "-o", name])
            if rc != 0 or out.strip() != value:
                wrong.append(f"{name}: 期待 {value}、実際 {out.strip()}")
        record("統一したサーバー設定", not wrong, "、".join(wrong))
        agent = load_agent()
        try:
            agent.start_and_watch(8)
            record("unbound ユニットの起動と安定", True)
        except agent.StepError as exc:
            record("unbound ユニットの起動と安定", False, exc.detail)
        sh(["unbound-control", "set_option", "val-log-level:", "2"])
        time.sleep(2)
        try:
            agent.local_check(checks.get("checks", {}))
            record("名前解決と DNSSEC の確認", True)
        except agent.StepError as exc:
            names = list(checks.get("checks", {}).get("resolve", [])) + [checks.get("checks", {}).get("dnssec_valid", "")]
            print("[診断] 名前解決と DNSSEC の確認に失敗したため、詳しい情報を出力します。", flush=True)
            print(diagnose([name for name in names if name]), flush=True)
            rc, out = sh(["journalctl", "-u", "unbound", "-n", "60", "--no-pager"])
            print("[診断] unbound の直近のログ（journalctl -u unbound、60 行）:\n" + out, flush=True)
            record("名前解決と DNSSEC の確認", False, exc.detail + "\n" + out[-600:])
    ok = all(r["ok"] for r in RESULTS)
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump({"ok": ok, "results": RESULTS}, handle, ensure_ascii=False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
