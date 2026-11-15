import fnmatch
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from .util import UbpError

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}")
COMMIT_RE = re.compile(r"\b[0-9a-f]{40}\b")
GIT_ENV = {
    "GIT_AUTHOR_NAME": "unbound-packager",
    "GIT_AUTHOR_EMAIL": "root@localhost",
    "GIT_COMMITTER_NAME": "unbound-packager",
    "GIT_COMMITTER_EMAIL": "root@localhost",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


class PatchError(UbpError):
    pass


ACTION_LABELS = {"adopt": "採用", "rebased": "当て直して採用", "drop": "除外"}
ACTION_VERBS = {"adopt": "を採用しました", "rebased": "を当て直して採用しました", "drop": "を除外しました"}


def describe(name, action, reason):
    return f"パッチ {name} {ACTION_VERBS.get(action, 'の判定: ' + action)}。{reason}"


@dataclass
class Decision:
    name: str
    action: str
    reason: str
    new_path: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        return {"name": self.name, "action": self.action, "reason": self.reason}


def _patch(tree, patch_file, strip, *args):
    proc = subprocess.run(
        ["patch", f"-p{int(strip)}", "-f", "-s", "-i", os.path.abspath(patch_file), *args],
        cwd=tree,
        capture_output=True,
    )
    return proc.returncode == 0


def applies(tree, patch_file, strip=1):
    return _patch(tree, patch_file, strip, "--fuzz=0", "--dry-run")


def applies_reversed(tree, patch_file, strip=1):
    return _patch(tree, patch_file, strip, "-R", "--fuzz=0", "--dry-run")


def apply(tree, patch_file, fuzz=0, strip=1):
    if not _patch(tree, patch_file, strip, f"--fuzz={fuzz}", "--no-backup-if-mismatch"):
        raise PatchError(f"パッチを当てられません: {os.path.basename(patch_file)}")


def header(text):
    lines = []
    for line in text.splitlines():
        if line.startswith(("diff ", "--- ", "Index: ")):
            break
        lines.append(line)
    return "\n".join(lines)


def path_prefixes(text, strip):
    if strip == 0:
        return ["--no-prefix"]
    extra = ""
    for match in re.finditer(r"(?m)^(?:---|\+\+\+) (\S+)", text):
        if match.group(1) == "/dev/null":
            continue
        parts = match.group(1).split("/")
        if len(parts) > strip:
            extra = "".join(part + "/" for part in parts[1:strip])
        break
    return [f"--src-prefix=a/{extra}", f"--dst-prefix=b/{extra}"]


def references(name, text):
    head = header(text)
    cves = sorted(set(CVE_RE.findall(name + "\n" + head)))
    commits = sorted(set(COMMIT_RE.findall(head)))
    return cves, commits


def _git(repo, *args, check=True):
    env = dict(os.environ)
    env.update(GIT_ENV)
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, env=env)
    if check and proc.returncode != 0:
        raise PatchError("git の実行に失敗しました: " + proc.stderr.decode("utf-8", "replace")[-400:])
    return proc


def _sync(src, dst):
    for entry in os.listdir(dst):
        if entry == ".git":
            continue
        target = os.path.join(dst, entry)
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        else:
            os.unlink(target)
    for entry in os.listdir(src):
        if entry == ".git":
            continue
        source = os.path.join(src, entry)
        target = os.path.join(dst, entry)
        if os.path.isdir(source) and not os.path.islink(source):
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target, follow_symlinks=False)


def three_way(old_tree, new_tree, patch_file, workdir, out_path, strip=1):
    repo = os.path.join(workdir, "merge")
    shutil.rmtree(repo, ignore_errors=True)
    os.makedirs(repo)
    _git(repo, "init", "-q", "-b", "base")
    _git(repo, "config", "core.autocrlf", "false")
    _sync(old_tree, repo)
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "old")
    _git(repo, "checkout", "-q", "-b", "patched")
    apply(repo, patch_file, fuzz=2, strip=strip)
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "patch")
    _git(repo, "checkout", "-q", "-b", "new", "base")
    _sync(new_tree, repo)
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "new")
    pick = _git(repo, "cherry-pick", "--allow-empty", "--keep-redundant-commits", "patched", check=False)
    if pick.returncode != 0:
        _git(repo, "cherry-pick", "--abort", check=False)
        shutil.rmtree(repo, ignore_errors=True)
        return False
    original = open(patch_file, encoding="utf-8", errors="replace").read()
    diff = _git(repo, "diff", "--no-color", "--no-ext-diff", *path_prefixes(original, strip), "HEAD~1", "HEAD").stdout
    shutil.rmtree(repo, ignore_errors=True)
    if not diff.strip():
        return None
    head = header(original)
    with open(out_path, "wb") as handle:
        if head.strip():
            handle.write(head.rstrip("\n").encode("utf-8") + b"\n\n")
        handle.write(diff)
    return True


def decide(patches, old_tree, new_tree, new_version, drop_globs, workdir, commit_checker=None, advisory=None):
    os.makedirs(workdir, exist_ok=True)
    changelog = ""
    changelog_path = os.path.join(new_tree, "doc", "Changelog")
    if os.path.exists(changelog_path):
        changelog = open(changelog_path, encoding="utf-8", errors="replace").read()
    old_cur = os.path.join(workdir, "old-cur")
    new_cur = os.path.join(workdir, "new-cur")
    for src, dst in ((old_tree, old_cur), (new_tree, new_cur)):
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, symlinks=True)
    decisions = []
    for item in patches:
        name, path = item[0], item[1]
        strip = item[2] if len(item) > 2 else 1
        text = open(path, encoding="utf-8", errors="replace").read()
        decision = _decide_one(name, path, text, old_cur, new_cur, new_version, drop_globs, workdir, changelog, commit_checker, strip, advisory)
        decisions.append(decision)
        try:
            apply(old_cur, path, fuzz=2, strip=strip)
        except PatchError:
            decision.extra["old_apply_failed"] = True
    shutil.rmtree(old_cur, ignore_errors=True)
    shutil.rmtree(new_cur, ignore_errors=True)
    return decisions


def _decide_one(name, path, text, old_cur, new_cur, new_version, drop_globs, workdir, changelog, commit_checker, strip=1, advisory=None):
    for glob in drop_globs:
        if fnmatch.fnmatch(name, glob):
            return Decision(name, "drop", f"統一方針で採用しないパッチです（`{glob}` に一致）。")
    cves, commits = references(name, text)
    if cves and all(cve in changelog for cve in cves):
        return Decision(name, "drop", f"{'、'.join(cves)} の修正は、上流 {new_version} の Changelog に記載されています。")
    if cves and advisory:
        last = [advisory(cve) for cve in cves]
        if all(last) and all(_version(item) < _version(new_version) for item in last):
            detail = "、".join(f"{cve} の影響は {item} まで" for cve, item in zip(cves, last))
            return Decision(name, "drop", f"NLnet Labs の告知では、{detail}で、{new_version} は影響を受けません。")
    if commit_checker:
        for commit in commits:
            if commit_checker(commit, new_version):
                return Decision(name, "drop", f"上流のコミット {commit[:12]} は {new_version} に含まれています。")
    if applies_reversed(new_cur, path, strip):
        return Decision(name, "drop", "新しい版にすでに取り込まれています（逆向きに当てられることで確認しました）。")
    if applies(new_cur, path, strip):
        apply(new_cur, path, strip=strip)
        return Decision(name, "adopt", "新しい版にそのまま当てられます。")
    out_path = os.path.join(workdir, "rebased-" + name)
    result = three_way(old_cur, new_cur, path, workdir, out_path, strip)
    if result is None:
        return Decision(name, "drop", "当て直すと差分がなくなるため、新しい版にすでに取り込まれています。")
    if result is False:
        raise PatchError(
            f"パッチ {name} は、そのままでも逆向きでも当たらず、3-way merge でも当て直せませんでした。人の判断が必要です。"
        )
    apply(new_cur, out_path, strip=strip)
    return Decision(name, "rebased", "元のパッチを、3-way マージで新しい版に合わせました。", new_path=out_path)


def _version(text):
    return tuple(int(part) for part in text.split("."))


def parse_advisories(page):
    import html

    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"\s+", " ", text)
    found = {}
    for match in re.finditer(r"CVE:\s*(CVE-\d{4}-\d{4,})(.*?)(?=CVE:\s*CVE-\d|\Z)", text):
        affects = re.search(r"Affects:(.*?)(?:Not affected:|Severity:|Impact:|$)", match.group(2))
        if not affects:
            continue
        listed = re.findall(r"\b\d+\.\d+\.\d+\b", affects.group(1))
        if listed and match.group(1) not in found:
            found[match.group(1)] = max(listed, key=_version)
    return found


def advisory_checker(fetch, url="https://nlnetlabs.nl/projects/unbound/security-advisories/"):
    cache = {}

    def check(cve):
        if "table" not in cache:
            try:
                cache["table"] = parse_advisories(fetch(url).decode("utf-8", "replace"))
            except Exception:
                cache["table"] = {}
        return cache["table"].get(cve)

    return check


def github_commit_checker(fetch):
    import json

    def check(commit, version):
        url = f"https://api.github.com/repos/NLnetLabs/unbound/compare/{commit}...release-{version}"
        try:
            data = json.loads(fetch(url))
        except Exception:
            return False
        return data.get("status") in ("ahead", "identical")

    return check
