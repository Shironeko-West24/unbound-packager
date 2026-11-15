import os
import re
import shutil
import tempfile

from .util import UbpError, add_secret, run

IDENTITY = "unbound-packager"
NAMESPACE = "unbound-packager-release"
PUBLIC_KEY_FILE = os.path.join("packaging", "keys", "release-signing.pub")
KEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]+={0,2}( [ -~]*)?$")
SUMS = "SHA256SUMS"
SIGNATURE = "SHA256SUMS.sig"


def public_key(code):
    path = os.path.join(str(code), PUBLIC_KEY_FILE)
    try:
        key = open(path, encoding="utf-8").read().strip()
    except OSError:
        raise UbpError(f"リリースの署名の公開鍵がありません: {PUBLIC_KEY_FILE}")
    if not KEY_RE.match(key):
        raise UbpError(f"リリースの署名の公開鍵の形式が正しくありません: {PUBLIC_KEY_FILE}")
    return key


def allowed_signers_line(key):
    return f'{IDENTITY} namespaces="{NAMESPACE}" {key}\n'


def generate(directory, comment="unbound-packager release"):
    key_file = os.path.join(str(directory), "release-signing")
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", key_file])
    return key_file, open(key_file + ".pub", encoding="utf-8").read().strip()


def sign(key_file, sums_path):
    signature = sums_path + ".sig"
    if os.path.exists(signature):
        os.unlink(signature)
    run(["ssh-keygen", "-Y", "sign", "-f", key_file, "-n", NAMESPACE, sums_path])
    return signature


def verify(key, sums_path, signature):
    work = tempfile.mkdtemp(prefix="ubp-verify-")
    try:
        signers = os.path.join(work, "allowed_signers")
        with open(signers, "w", encoding="utf-8") as handle:
            handle.write(allowed_signers_line(key))
        with open(sums_path, "rb") as handle:
            data = handle.read()
        rc, out, err = run(
            ["ssh-keygen", "-Y", "verify", "-f", signers, "-I", IDENTITY, "-n", NAMESPACE, "-s", signature],
            check=False, input=data,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rc == 0


def sign_release(target, private_key, expected_key):
    if not private_key.strip():
        raise UbpError("シークレット RELEASE_SIGNING_KEY がないため、リリースに署名できません。")
    add_secret(private_key.strip())
    sums_path = os.path.join(str(target), SUMS)
    if not os.path.isfile(sums_path):
        raise UbpError(f"{SUMS} がないため、署名できません。")
    work = tempfile.mkdtemp(prefix="ubp-sign-")
    try:
        key_file = os.path.join(work, "key")
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(private_key.strip() + "\n")
        signature = sign(key_file, sums_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not verify(expected_key, sums_path, signature):
        os.unlink(signature)
        raise UbpError(f"署名を、リポジトリの公開鍵（{PUBLIC_KEY_FILE}）で確かめられませんでした。シークレットの秘密鍵と公開鍵が対応しているか確かめてください。")
    return signature
