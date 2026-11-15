#!/usr/bin/python3 -I
import json
import os
import re
import shutil
import sys

NAME = os.path.basename(sys.argv[0])
ARGS = sys.argv[1:]
FAKE = os.environ.get("UBP_FAKE_DIR", "")
ROOT = os.environ.get("UBP_TEST_ROOT", "")


def record():
    with open(os.path.join(FAKE, "calls.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"cmd": NAME, "args": ARGS}, ensure_ascii=False) + "\n")


def rule():
    path = os.path.join(FAKE, "rules.json")
    if not os.path.exists(path):
        return None
    rules = json.load(open(path, encoding="utf-8"))
    joined = " ".join(ARGS)
    for index, item in enumerate(rules):
        if item["cmd"] != NAME or item.get("contains") and item["contains"] not in joined:
            continue
        if "times" in item:
            if item["times"] <= 0:
                continue
            rules[index]["times"] -= 1
            json.dump(rules, open(path, "w", encoding="utf-8"))
        return item
    return None


def installed_file():
    return os.path.join(ROOT, "fake-installed")


def emulate_install():
    for arg in ARGS:
        base = os.path.basename(arg)
        match = re.match(r"^unbound-(\d.*)\.x86_64\.rpm$", base) or re.match(r"^unbound_(\d.*)_amd64\.deb$", base)
        if match:
            open(installed_file(), "w").write(match.group(1))


def main():
    record()
    matched = rule()
    if matched:
        sys.stdout.write(matched.get("stdout", ""))
        sys.stderr.write(matched.get("stderr", ""))
        return matched.get("rc", 0)
    if NAME == "systemctl":
        if "show" in ARGS:
            print("ActiveState=active\nSubState=running\nNRestarts=0\nMainPID=100")
        elif "is-active" in ARGS:
            print("inactive" if "firewalld" in ARGS else "active")
        elif "is-system-running" in ARGS:
            print("running")
        return 0
    if NAME == "rpm":
        if "-q" in ARGS:
            if os.path.exists(installed_file()):
                sys.stdout.write(open(installed_file()).read())
                return 0
            return 1
        emulate_install()
        return 0
    if NAME == "dpkg-query":
        if os.path.exists(installed_file()):
            sys.stdout.write("ii |" + open(installed_file()).read())
            return 0
        return 1
    if NAME == "dnf":
        if "install" in ARGS and "--downloadonly" not in ARGS:
            emulate_install()
        return 0
    if NAME == "apt-get":
        if "install" in ARGS and "-s" not in ARGS:
            emulate_install()
        return 0
    if NAME == "podman":
        if ARGS[:1] == ["run"] and os.environ.get("UBP_FAKE_PODMAN_OUT"):
            for index, arg in enumerate(ARGS):
                if arg == "-v" and ARGS[index + 1].endswith(":/work:Z"):
                    work = ARGS[index + 1].split(":")[0]
                    shutil.copytree(os.environ["UBP_FAKE_PODMAN_OUT"], os.path.join(work, "out"), dirs_exist_ok=True)
        return 0
    if NAME == "ufw" and "status" in ARGS:
        print("Status: inactive")
    return 0


sys.exit(main())
