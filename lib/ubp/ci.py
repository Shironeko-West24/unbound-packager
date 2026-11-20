import argparse
import contextlib
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

from . import containers, notify, release, signing, upstream, versions
from .github import GitHub, http_transport
from .util import LOG, UbpError, add_secret, eprint, log, run

CODE = Path(__file__).resolve().parents[2]
DOCUMENTS = ("README.md", "SECURITY.md", "NOTICE.md", "CLAUDE.md", "LICENSE")
DOCUMENT_DIRS = ("docs/",)


def env(name, default=""):
    return os.environ.get(name, default)


def work_dir():
    if env("UBP_WORK"):
        return Path(env("UBP_WORK"))
    if env("RUNNER_TEMP"):
        return Path(env("RUNNER_TEMP")) / "ubp"
    return CODE / "work"


def options():
    return json.loads((CODE / "packaging" / "options.json").read_text(encoding="utf-8"))


def output(**values):
    path = env("GITHUB_OUTPUT")
    for key, value in values.items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        log(f"{key}={text}")
        if path:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"{key}={text}\n")


def github():
    token = env("GH_TOKEN")
    add_secret(token)
    return GitHub(env("GITHUB_REPOSITORY"), http_transport(token))


def notifier():
    return notify.Notifier(env("DISCORD_WEBHOOK_URL"))


def run_url():
    return f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{env('GITHUB_REPOSITORY')}/actions/runs/{env('GITHUB_RUN_ID')}"


@contextlib.contextmanager
def stage(title, record):
    record["stage"] = title
    print(f"::group::{title}", flush=True)
    try:
        yield
    finally:
        print("::endgroup::", flush=True)


def decide(requested, rebuild, manual, tags, attempts, maximum, latest):
    version = requested or latest
    versions.parse(version)
    revs = release.revisions(tags, version)
    if rebuild:
        return {"version": version, "revision": (revs[-1] if revs else 0) + 1, "needed": True, "reason": "作り直しの指定"}
    if revs:
        return {"version": version, "revision": revs[-1], "needed": False, "reason": "公開済み"}
    if attempts >= maximum and not manual:
        return {"version": version, "revision": 1, "needed": False, "reason": f"{maximum} 回失敗したため、自動では試さない"}
    return {"version": version, "revision": 1, "needed": True, "reason": "未公開のバージョン"}


def cmd_detect(args):
    requested = env("INPUT_VERSION").strip()
    latest = requested or upstream.detect_latest()
    gh = github()
    tags = gh.release_tags()
    attempts = gh.attempts(latest)
    result = decide(
        requested, env("INPUT_REBUILD") == "true", env("GITHUB_EVENT_NAME") == "workflow_dispatch",
        tags, attempts, options()["max_attempts"], latest,
    )
    log(f"上流のバージョン: {result['version']}（{result['reason']}）")
    published = sorted((p for p in map(release.parse_tag, tags) if p), key=lambda p: (versions.parse(p[0]), p[1]))
    output(
        version=result["version"], revision=result["revision"], needed=result["needed"],
        attempt=attempts + 1, first=result["needed"] and attempts == 0,
        published=release.tag_name(*published[-1]) if published else "",
    )
    return 0


def cmd_latest(args):
    output(version=upstream.detect_latest())
    return 0


def documents_only(files):
    return bool(files) and all(name in DOCUMENTS or name.startswith(DOCUMENT_DIRS) for name in files)


def changed_files(base, head, cwd):
    run(["git", "fetch", "-q", "--depth", "200", "origin", base, head], cwd=cwd, timeout=300)
    rc, out, _ = run(["git", "merge-base", base, head], check=False, cwd=cwd)
    if rc != 0:
        return None
    _, out, _ = run(["git", "diff", "--name-only", "--no-renames", "-z", out.strip(), head], cwd=cwd)
    return [name for name in out.split("\0") if name]


def cmd_changes(args):
    base, head = env("UBP_BASE_SHA"), env("UBP_HEAD_SHA")
    if env("GITHUB_EVENT_NAME") != "pull_request" or not base or not head:
        log("プルリクエスト以外の実行のため、ビルドとテストも行います。")
        output(build=True)
        return 0
    try:
        files = changed_files(base, head, str(CODE))
    except UbpError as exc:
        log(str(exc))
        files = None
    if files is None:
        log("変更されたファイルを調べられなかったため、ビルドとテストも行います。")
        build = True
    elif documents_only(files):
        log(f"文書だけの変更です（{len(files)} ファイル）。ビルドとテストは省きます。")
        build = False
    else:
        log("文書以外の変更があるため、ビルドとテストを行います。")
        build = True
    output(build=build)
    return 0


def cmd_pipeline(args):
    version = args.version or env("UBP_VERSION")
    revision = int(args.revision or env("UBP_REVISION", "1"))
    versions.parse(version)
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    os_names = args.os or containers.OS_NAMES
    opts = options()
    record = {"stage": "準備", "detail": ""}
    failure = work / "failure.json"
    if failure.exists():
        failure.unlink()
    try:
        with stage("ソースの取得と署名の検証", record):
            source = upstream.download(CODE, work, version)
        for os_name in os_names:
            with stage(f"ビルド（{os_name}）", record):
                containers.build_packages(CODE, work, os_name, version, revision, source, args.jobs)
        problems = []
        for os_name in os_names:
            with stage(f"パッケージのテスト（{os_name}）", record):
                try:
                    containers.test_packages(CODE, work, os_name, opts["checks"])
                except UbpError as exc:
                    log(str(exc))
                    problems.append(str(exc))
        if problems:
            record["stage"] = "パッケージのテスト"
            raise UbpError("\n".join(problems))
        if set(os_names) == set(containers.OS_NAMES):
            with stage("リリースの組み立て", record):
                release.assemble(CODE, work, version, revision)
            sys.path.insert(0, str(CODE / "tests" / "container"))
            import server_test

            for os_name in os_names:
                with stage(f"サーバー側コマンドの実地テスト（{os_name}）", record):
                    server_test.run(CODE, work, os_name, release.tag_name(version, revision), opts["checks"])
    except UbpError as exc:
        record["detail"] = str(exc)
        failure.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        log(f"ビルドとテスト: NG（{record['stage']}）\n{exc}")
        return 1
    log("ビルドとテスト: すべて OK")
    return 0


def load_combined():
    path = work_dir() / "release" / "manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UbpError("公開する内容がありません。先に pipeline を実行してください。")


def cmd_sign(args):
    load_combined()
    key = signing.public_key(CODE)
    signing.sign_release(str(work_dir() / "release"), env("RELEASE_SIGNING_KEY"), key)
    log(f"SHA256SUMS に署名し、リポジトリの公開鍵で確かめました（{key.split()[0]}）。")
    return 0


def cmd_publish(args):
    combined = load_combined()
    repository = env("GITHUB_REPOSITORY")
    github()
    tag = release.publish(str(work_dir() / "release"), combined, repository, env("GITHUB_SHA"))
    log(f"公開しました: {tag}")
    output(tag=tag)
    return 0


def cmd_notify_new(args):
    notifier().send(*notify.new_version(env("UBP_VERSION"), run_url()))
    return 0


def cmd_on_success(args):
    combined = load_combined()
    version = combined["upstream"]
    repository = env("GITHUB_REPOSITORY")
    url = f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{repository}/releases/tag/{combined['tag']}"
    try:
        github().close_failure(version, f"バージョン {version} のビルドとテストに合格し、公開しました（{combined['tag']}）。")
    except UbpError as exc:
        log(str(exc))
    text, unpackaged = release.summary(combined)
    notifier().send(*notify.released(version, combined["tag"], text, unpackaged, url))
    return 0


def cmd_on_failure(args):
    version = env("UBP_VERSION")
    maximum = options()["max_attempts"]
    record = {"stage": "準備または公開", "detail": "ログを確認してください。"}
    path = work_dir() / "failure.json"
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
    body = f"{record['stage']}に失敗しました。\n\n```\n{record['detail'][-3000:]}\n```\n\n実行の記録: {run_url()}"
    attempt = int(env("UBP_ATTEMPT", "1"))
    try:
        attempt = github().record_failure(version, body)
    except UbpError as exc:
        log(str(exc))
    sender = notifier()
    sender.send(*notify.failed(version, record["stage"], record["detail"], attempt, maximum, run_url()))
    if attempt >= maximum:
        sender.send(*notify.gave_up(version, maximum))
    return 0


def cmd_keepalive(args):
    name = env("UBP_WORKFLOW_FILE", "release.yml")
    try:
        ok = github().enable_workflow(name)
        log("ワークフローを有効化し直しました。" if ok else "ワークフローの有効化は受け付けられませんでした。")
    except UbpError as exc:
        log(str(exc))
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    if env("GITHUB_EVENT_NAME") == "schedule" and today.day == 1:
        notifier().send(*notify.heartbeat(env("UBP_VERSION") or "不明", env("UBP_PUBLISHED")))
    return 0


def cmd_selftest(args):
    return subprocess.run([sys.executable, "-I", str(CODE / "tests" / "run_all.py")]).returncode


COMMANDS = {
    "detect": cmd_detect,
    "latest": cmd_latest,
    "changes": cmd_changes,
    "pipeline": cmd_pipeline,
    "sign": cmd_sign,
    "publish": cmd_publish,
    "notify-new": cmd_notify_new,
    "on-success": cmd_on_success,
    "on-failure": cmd_on_failure,
    "keepalive": cmd_keepalive,
    "selftest": cmd_selftest,
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ci.py", description="GitHub Actions から呼ぶ処理")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        item = sub.add_parser(name)
        if name == "pipeline":
            item.add_argument("--version")
            item.add_argument("--revision")
            item.add_argument("--os", action="append", choices=containers.OS_NAMES)
            item.add_argument("--jobs", type=int, default=0)
    args = parser.parse_args(argv)
    LOG.echo = True
    try:
        return COMMANDS[args.command](args)
    except UbpError as exc:
        eprint("エラー: " + str(exc))
        return exc.code
