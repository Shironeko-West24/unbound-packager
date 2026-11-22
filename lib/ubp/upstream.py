import os
import re
import shutil
import tempfile
import urllib.request

from . import versions
from .util import UbpError, log, run, sha256_file

BASE_URL = "https://nlnetlabs.nl/downloads/unbound/"
KEY_URL = "https://nlnetlabs.nl/downloads/keys/releases-g2.asc"
KEY_NAME = "nlnetlabs-releases-g2.asc"
FINGERPRINT = "231018690C4D903EF419146AA144323DEAACDF45"


def fetch(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": "unbound-packager/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise UbpError(f"取得できませんでした: {url}（{type(exc).__name__}）")


def detect_latest(fetcher=fetch):
    html = fetcher(BASE_URL).decode("utf-8", "replace")
    found = versions.versions_in_index(html)
    if not found:
        raise UbpError("上流の配布ページからバージョン番号を読み取れませんでした。")
    return versions.latest(found)


def key_fingerprints(key_file):
    home = tempfile.mkdtemp(prefix="ubp-gpg-")
    try:
        _, out, _ = run(["gpg", "--homedir", home, "--batch", "--with-colons", "--show-keys", key_file])
    finally:
        shutil.rmtree(home, ignore_errors=True)
    found = []
    primary = False
    for line in out.splitlines():
        parts = line.split(":")
        if parts[0] == "pub":
            primary = True
        elif parts[0] == "sub":
            primary = False
        elif parts[0] == "fpr" and primary:
            found.append(parts[9])
            primary = False
    return found


def check_key(key_file):
    found = key_fingerprints(key_file)
    if found != [FINGERPRINT]:
        raise UbpError(
            "署名鍵の指紋が固定値と一致しません。取り込みません。\n"
            f"  固定値: {FINGERPRINT}\n  ファイル: {'、'.join(found) or '（鍵なし）'}"
        )


def obtain_key(code, cache, fetcher=fetch):
    bundled = os.path.join(str(code), "packaging", "keys", KEY_NAME)
    if os.path.isfile(bundled):
        check_key(bundled)
        return bundled
    os.makedirs(str(cache), exist_ok=True)
    target = os.path.join(str(cache), KEY_NAME)
    tmp = target + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(fetcher(KEY_URL))
    try:
        check_key(tmp)
    except UbpError:
        os.unlink(tmp)
        raise
    os.replace(tmp, target)
    return target


def verify_signature(key_file, signature, data_file, fingerprint=FINGERPRINT):
    home = tempfile.mkdtemp(prefix="ubp-gpg-")
    try:
        run(["gpg", "--homedir", home, "--batch", "--quiet", "--import", key_file])
        rc, out, _ = run(
            ["gpg", "--homedir", home, "--batch", "--status-fd", "1", "--verify", signature, data_file],
            check=False,
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "VALIDSIG":
            if rc == 0 and fingerprint in (parts[2], parts[-1]):
                return True
    raise UbpError(f"署名を検証できませんでした: {os.path.basename(str(data_file))}")


def download(code, work, version, fetcher=fetch):
    versions.parse(version)
    target = os.path.join(str(work), "source")
    os.makedirs(target, exist_ok=True)
    key = obtain_key(code, target, fetcher)
    name = f"unbound-{version}.tar.gz"
    tarball = os.path.join(target, name)
    signature = tarball + ".asc"
    log(f"上流のソース {name} を取得します。")
    expected = fetcher(BASE_URL + name + ".sha256").decode("ascii", "replace").split()
    if not expected or not re.match(r"^[0-9a-f]{64}$", expected[0]):
        raise UbpError("上流の SHA-256 ファイルを読み取れませんでした。")
    with open(tarball, "wb") as handle:
        handle.write(fetcher(BASE_URL + name))
    if sha256_file(tarball) != expected[0]:
        os.unlink(tarball)
        raise UbpError(f"SHA-256 が一致しません: {name}")
    with open(signature, "wb") as handle:
        handle.write(fetcher(BASE_URL + name + ".asc"))
    verify_signature(key, signature, tarball)
    log(f"署名を検証しました: {name}（鍵の指紋 {FINGERPRINT}）")
    return {"tarball": tarball, "signature": signature, "key": key}
