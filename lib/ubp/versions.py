import re

from .util import UbpError

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
INDEX_RE = re.compile(r"unbound-(\d+\.\d+\.\d+)\.tar\.gz(?![.\w])")
DIST_RE = re.compile(r"\.(el\d+(?:_\d+)?)")


def parse(version):
    match = VERSION_RE.match(version or "")
    if not match:
        raise UbpError(f"版番号の形式が正しくありません（例 1.26.2）: {version}")
    return tuple(int(part) for part in match.groups())


def is_release(version):
    return bool(VERSION_RE.match(version or ""))


def latest(versions):
    releases = [v for v in versions if is_release(v)]
    if not releases:
        return None
    return max(releases, key=parse)


def versions_in_index(html):
    return sorted(set(INDEX_RE.findall(html)), key=parse)


def rpm_dist(release):
    match = DIST_RE.search(release or "")
    return "." + match.group(1) if match else None


def rpm_release(revision, dist):
    return f"{int(revision)}.local{dist or ''}"


def rpm_evr(version, revision, dist):
    parse(version)
    return f"{version}-{rpm_release(revision, dist)}"


def deb_version(version, revision, epoch=None):
    parse(version)
    prefix = f"{epoch}:" if epoch else ""
    return f"{prefix}{version}-0+local{int(revision)}"


def deb_epoch(old_version):
    match = re.match(r"^(\d+):", old_version or "")
    return match.group(1) if match else None
