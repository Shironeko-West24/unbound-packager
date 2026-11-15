import json
import os
import shutil
import time

from .patches import describe
from .util import UbpError, log, run, run_stream, sha256_file

SCRIPTS = {"rocky10": "rpm_build.py", "ubuntu2604": "deb_build.py"}
OS_NAMES = ["rocky10", "ubuntu2604"]


def engine():
    return os.environ.get("UBP_ENGINE", "podman").split()


def fix_owner(path):
    if engine()[0] == "sudo":
        run(["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(path)], check=False)


def build_image(code, kind):
    tag = f"localhost/ubp-{kind}:latest"
    log(f"コンテナイメージ {tag} を用意します。")
    rc = run_stream(engine() + ["build", "--pull=newer", "-t", tag, os.path.join(str(code), "containers", kind)])
    if rc != 0:
        raise UbpError(f"コンテナイメージを作れませんでした: {kind}")
    return tag


def prepare_source(code, target):
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(os.path.join(target, "lib"))
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(os.path.join(str(code), "lib", "ubp"), os.path.join(target, "lib", "ubp"), ignore=ignore)
    shutil.copytree(os.path.join(str(code), "packaging"), os.path.join(target, "packaging"), ignore=ignore)
    shutil.copytree(os.path.join(str(code), "server"), os.path.join(target, "server"), ignore=ignore)


def remove(name):
    run(engine() + ["rm", "-f", "-t", "5", name], check=False)


def build_packages(code, work, os_name, version, revision, source, jobs=0):
    job = os.path.join(str(work), "build", os_name)
    fix_owner(job)
    shutil.rmtree(job, ignore_errors=True)
    os.makedirs(os.path.join(job, "in"))
    os.makedirs(os.path.join(job, "out"))
    prepare_source(code, os.path.join(job, "src"))
    shutil.copy2(source["tarball"], os.path.join(job, "in"))
    shutil.copy2(source["signature"], os.path.join(job, "in"))
    shutil.copy2(source["key"], os.path.join(job, "in", "signing-key.asc"))
    image = build_image(code, f"build-{os_name}")
    name = f"ubp-build-{os_name}"
    remove(name)
    log(f"{os_name} 向けのビルドを始めます（版 {version}、リビジョン {revision}）。")
    rc = run_stream(engine() + [
        "run", "--rm", "--name", name, "-v", f"{job}:/work:Z", image,
        "python3", "-I", f"/work/src/packaging/{SCRIPTS[os_name]}",
        "--version", version, "--revision", str(revision), "--jobs", str(jobs),
    ])
    remove(name)
    fix_owner(job)
    manifest_path = os.path.join(job, "out", "manifest.json")
    if rc != 0 or not os.path.exists(manifest_path):
        detail = ""
        error_file = os.path.join(job, "out", "error.txt")
        if os.path.exists(error_file):
            detail = open(error_file, encoding="utf-8").read().strip()
        raise UbpError(f"{os_name} 向けのビルドに失敗しました。{detail}")
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    for item in manifest["files"] + manifest.get("sources", []):
        if sha256_file(os.path.join(job, "out", item["file"])) != item["sha256"]:
            raise UbpError(f"成果物の SHA-256 が一致しません: {item['file']}")
    target = os.path.join(str(work), "out", os_name)
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copytree(os.path.join(job, "out"), target)
    shutil.rmtree(job, ignore_errors=True)
    log(f"{os_name}: ビルドに成功しました（{manifest['pkg_version']}、パッケージ {len(manifest['files'])} 個）。")
    for patch in manifest["patches"]:
        log("  " + describe(patch["name"], patch["action"], patch["reason"]))
    return manifest


def wait_systemd(name, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _, out, _ = run(engine() + ["exec", name, "systemctl", "is-system-running"], check=False)
        if out.strip() in ("running", "degraded"):
            return
        time.sleep(2)
    raise UbpError(f"テスト用コンテナ {name} の systemd が起動しませんでした。")


def start_systemd(code, kind, name, mounts):
    image = build_image(code, kind)
    remove(name)
    cmd = engine() + ["run", "-d", "--name", name, "--systemd=always"]
    for source, target, mode in mounts:
        cmd += ["-v", f"{source}:{target}:{mode}"]
    run(cmd + [image])
    wait_systemd(name)


def test_packages(code, work, os_name, checks):
    from .release import encode_args

    artifact = os.path.join(str(work), "out", os_name)
    if not os.path.exists(os.path.join(artifact, "manifest.json")):
        raise UbpError(f"{os_name}: 成果物がありません。")
    src = os.path.join(str(work), "test", os_name, "src")
    prepare_source(code, src)
    name = f"ubp-test-{os_name}"
    start_systemd(code, f"test-{os_name}", name, [(artifact, "/pkgs", "ro,Z"), (src, "/ubp", "ro,Z")])
    log(f"{os_name}: パッケージのテストを始めます。")
    try:
        rc = run_stream(engine() + [
            "exec", name, "python3", "-I", "/ubp/packaging/intest.py",
            "--checks", encode_args({"checks": checks}), "--report", "/tmp/report.json",
        ])
        _, out, _ = run(engine() + ["exec", name, "cat", "/tmp/report.json"], check=False)
    finally:
        remove(name)
    try:
        report = json.loads(out)
    except ValueError:
        report = {"ok": False, "results": [{"name": "テストの実行", "ok": False, "detail": "結果を読めませんでした。"}]}
    with open(os.path.join(artifact, "test-report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    failed = [r for r in report["results"] if not r["ok"]]
    if rc != 0 or not report.get("ok") or failed:
        lines = [f"  ✗ {r['name']}: {r.get('detail', '')}" for r in failed]
        raise UbpError(f"{os_name}: パッケージのテストに失敗しました。\n" + "\n".join(lines))
    log(f"{os_name}: パッケージのテストに合格しました（{len(report['results'])} 項目）。")
    return report
