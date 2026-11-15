import datetime
import hashlib
import os
import subprocess
import sys


class UbpError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


SECRETS = []


def add_secret(value):
    if value and value not in SECRETS:
        SECRETS.append(value)


def redact(text):
    for secret in SECRETS:
        text = text.replace(secret, "［秘密］")
    return text


class Log:
    def __init__(self):
        self.path = None
        self.echo = True

    def write(self, message):
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for part in redact(str(message)).splitlines() or [""]:
            line = f"{stamp} {part}"
            if self.echo:
                print(line, flush=True)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")


LOG = Log()


def log(message):
    LOG.write(message)


def today():
    return datetime.date.today().isoformat()


def timestamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def run(cmd, check=True, input=None, timeout=None, env=None, cwd=None):
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            input=input,
            capture_output=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError:
        raise UbpError(f"コマンドが見つかりません: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise UbpError(f"時間切れになりました: {redact(' '.join(str(c) for c in cmd[:3]))}")
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        tail = "\n".join((err or out).strip().splitlines()[-15:])
        raise UbpError(
            f"コマンドが失敗しました（終了コード {proc.returncode}）: "
            f"{redact(' '.join(str(c) for c in cmd[:4]))}\n{redact(tail)}"
        )
    return proc.returncode, out, err


def run_stream(cmd, env=None, cwd=None):
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError:
        raise UbpError(f"コマンドが見つかりません: {cmd[0]}")
    for raw in proc.stdout:
        log("  | " + raw.decode("utf-8", "replace").rstrip("\n"))
    return proc.wait()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_regular(path, limit=1 << 30):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        import stat

        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise UbpError(f"通常のファイルではありません: {path}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(fd)


def eprint(message):
    print(redact(str(message)), file=sys.stderr, flush=True)
