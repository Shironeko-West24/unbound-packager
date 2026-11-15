import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))

WORK = "/work"
IN = os.path.join(WORK, "in")
OUT = os.path.join(WORK, "out")


def say(message):
    print(f"[ubp] {message}", flush=True)


def fail(message):
    say("失敗: " + message)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "error.txt"), "w", encoding="utf-8") as handle:
        handle.write(message + "\n")
    sys.exit(1)


def sh(cmd, cwd=None, check=True, env=None, log=None):
    say("$ " + " ".join(cmd))
    if log:
        with open(log, "ab") as handle:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for line in proc.stdout:
                handle.write(line)
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
            rc = proc.wait()
    else:
        rc = subprocess.run(cmd, cwd=cwd, env=env).returncode
    if check and rc != 0:
        fail(f"コマンドが失敗しました（終了コード {rc}）: {' '.join(cmd[:4])}")
    return rc


def out(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True)
    if proc.returncode != 0:
        fail(f"コマンドが失敗しました: {' '.join(cmd[:4])}\n{proc.stderr.decode('utf-8', 'replace')[-600:]}")
    return proc.stdout.decode("utf-8", "replace")


def options():
    with open(os.path.join(HERE, "options.json"), encoding="utf-8") as handle:
        return json.load(handle)


def extract_stripped(tarball, target):
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    with tarfile.open(tarball) as tar:
        for member in tar.getmembers():
            parts = member.name.split("/")
            if member.name.startswith("/") or ".." in parts:
                fail(f"ソースの tar に危険な名前があります: {member.name}")
            if len(parts) < 2 or not parts[1]:
                continue
            member.name = "/".join(parts[1:])
            if member.islnk():
                member.linkname = "/".join(member.linkname.split("/")[1:])
            tar.extract(member, target, filter="tar")


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "unbound-packager/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one(pattern, what):
    found = sorted(glob.glob(pattern))
    if len(found) != 1:
        fail(f"{what}が 1 つに決まりません（{len(found)} 個）: {pattern}")
    return found[0]


def write_manifest(os_name, upstream, pkg_version, files, all_names, decisions, unpackaged, extra=None, sources=()):
    os.makedirs(OUT, exist_ok=True)
    entries = []
    for name, path in sorted(files):
        target = os.path.join(OUT, os.path.basename(path))
        shutil.copy2(path, target)
        entries.append({"name": name, "file": os.path.basename(path), "sha256": sha256(target)})
    source_entries = []
    for path in sorted(sources):
        target = os.path.join(OUT, os.path.basename(path))
        shutil.copy2(path, target)
        source_entries.append({"file": os.path.basename(path), "sha256": sha256(target)})
    manifest = {
        "os": os_name,
        "upstream": upstream,
        "pkg_version": pkg_version,
        "files": entries,
        "all_packages": sorted(all_names),
        "patches": [d.as_dict() for d in decisions],
        "unpackaged": unpackaged,
        "sources": source_entries,
    }
    manifest.update(extra or {})
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1)
    say(f"パッケージ {len(entries)} 個を出力しました。")
