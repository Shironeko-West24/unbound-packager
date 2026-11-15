import email.utils
import re

from .util import UbpError

def flag_feature(flag):
    match = re.match(r"^--(?:enable|disable|with|without)-([A-Za-z0-9_-]+?)(?:=.*)?$", flag)
    return match.group(1) if match else None


def strip_features(text, features):
    names = "|".join(re.escape(f) for f in sorted(features, key=len, reverse=True))
    token = re.compile(r"[ \t]*--(?:enable|disable|with|without)-(?:" + names + r")(?:=[^\s\\]*)?(?=[\s\\]|$)")
    return "\n".join(token.sub("", line) for line in text.split("\n"))


def configure_blocks(lines, anchor):
    pattern = re.compile(anchor)
    blocks = []
    index = 0
    while index < len(lines):
        if pattern.search(lines[index]):
            end = index
            while end + 1 < len(lines) and lines[end].rstrip().endswith("\\"):
                end += 1
            blocks.append((index, end))
            index = end + 1
        else:
            index += 1
    return blocks


def normalize_configure(text, canonical, anchor, exceptions=()):
    lines = text.split("\n")
    tail = []
    for index, line in enumerate(lines):
        if re.match(r"^%changelog\s*$", line):
            lines, tail = lines[:index], lines[index:]
            break
    blocks = configure_blocks(lines, anchor)
    if not blocks:
        raise UbpError("configure を呼ぶ行が見つからないため、ビルドオプションを統一できません。")
    inside = set()
    pattern = re.compile(anchor)
    for start, end in blocks:
        block = "\n".join(lines[start:end + 1])
        keep = set()
        for item in exceptions:
            if item["marker"] in block:
                keep |= set(item["keep"])
        applied = {key: value for key, value in canonical.items() if key not in keep}
        flags = " ".join(applied.values())
        for index in range(start, end + 1):
            inside.add(index)
            lines[index] = strip_features(lines[index], list(applied)) if applied else lines[index]
        lines[start] = pattern.sub(lambda m: f"{m.group(0)} {flags}", lines[start], count=1)
    for index, line in enumerate(lines):
        if index not in inside:
            lines[index] = strip_features(line, list(canonical))
    return "\n".join(lines + tail), len(blocks)


RPM_CONFIGURE_ANCHOR = r"^[ \t]*%\{?configure\}?(?=[\s\\]|$)"
DEB_CONFIGURE_ANCHOR = r"dh_auto_configure[^\n]*?--(?=[\s\\]|$)"


def spec_set_version_release(text, version, release):
    text, n1 = re.subn(r"(?m)^(Version:\s*).*$", lambda m: m.group(1) + version, text, count=1)
    text, n2 = re.subn(r"(?m)^(Release:\s*).*$", lambda m: m.group(1) + release, text, count=1)
    if n1 != 1 or n2 != 1:
        raise UbpError("spec に Version または Release の行が見つかりません。")
    return text


def spec_patches(text):
    found = []
    for match in re.finditer(r"(?mi)^Patch(\d*):\s*(\S+)\s*$", text):
        number = int(match.group(1)) if match.group(1) else 0
        found.append((number, match.group(2).rsplit("/", 1)[-1]))
    return found


def spec_patch_strip(expanded, raw, name):
    lines = expanded.splitlines()
    mention = re.compile(r"/" + re.escape(name) + r"(?=['\"\s;|<]|$)")
    for index, line in enumerate(lines):
        if not mention.search(line):
            continue
        for near in lines[index:index + 3]:
            match = re.search(r"\bpatch\b.*?(?:^|\s)-p\s*(\d+)\b", near)
            if match:
                return int(match.group(1))
    found = set(re.findall(r"(?m)^\s*%(?:autosetup|autopatch)\b[^\n]*?\s-p\s*(\d+)\b", raw))
    if len(found) == 1:
        return int(found.pop())
    raise UbpError(f"パッチ {name} の -p の値を spec から決められません。")


def spec_gpg_keyrings(expanded):
    return [match.rsplit("/", 1)[-1] for match in re.findall(r"--keyring=['\"]?([^'\"\s]+)", expanded)]


def spec_remove_patches(text, numbers):
    out = []
    numbers = set(numbers)
    for line in text.split("\n"):
        decl = re.match(r"(?i)^Patch(\d*):", line)
        if decl and (int(decl.group(1)) if decl.group(1) else 0) in numbers:
            continue
        apply = re.match(r"^\s*%patch(?:(\d+)\b|\s+(?:.*\s)?-P\s*(\d+)\b)", line)
        if apply and int(apply.group(1) or apply.group(2)) in numbers:
            continue
        out.append(line)
    return "\n".join(out)


def spec_add_changelog(text, version, release_plain, note, date_text, maintainer):
    entry = f"* {date_text} {maintainer} - {version}-{release_plain}\n- {note}\n\n"
    if re.search(r"(?m)^%changelog\s*$", text):
        return re.sub(r"(?m)^%changelog\s*$", lambda m: "%changelog\n" + entry.rstrip("\n") + "\n", text, count=1)
    return text.rstrip("\n") + "\n\n%changelog\n" + entry


def replace_module_config(text, remove):
    def fix(match):
        mods = [m for m in match.group(2).split() if m != remove]
        return f"{match.group(1)}\"{' '.join(mods)}\""

    return re.sub(r"(?m)^(\s*module-config:\s*)\"([^\"]*)\"", fix, text)


def set_server_option(text, name, value):
    pattern = re.compile(r"(?m)^([ \t]*)" + re.escape(name) + r":[ \t]*\S+[ \t]*$")
    return pattern.subn(lambda m: f"{m.group(1)}{name}: {value}", text)


def control_remove_build_dep(text, package):
    pattern = re.compile(r"(?m)^[ \t]*" + re.escape(package) + r"\b[^,\n]*,?[ \t]*\n")
    text2, count = pattern.subn("", text)
    if count == 0:
        inline = re.compile(r",?[ \t]*" + re.escape(package) + r"\b(?:[ \t]*\([^)]*\))?(?:[ \t]*\[[^\]]*\])?(?:[ \t]*<[^>]*>)*")
        text2, count = inline.subn("", text)
    return text2, count


def control_binary_packages(text):
    names = []
    for stanza in re.split(r"\n\s*\n", text):
        match = re.search(r"(?m)^Package:\s*(\S+)", stanza)
        if match:
            names.append(match.group(1))
    return names


def control_source_field(text, field):
    match = re.search(r"(?mi)^" + re.escape(field) + r":\s*(.+)$", text)
    return match.group(1).strip() if match else None


def changelog_top(text):
    match = re.match(r"^(\S+) \(([^)]+)\) ([^;]+);", text)
    if not match:
        raise UbpError("debian/changelog の先頭行を読めません。")
    return match.group(1), match.group(2), match.group(3).strip()


def changelog_entry(source, version, distribution, note, maintainer, when=None):
    date_text = email.utils.format_datetime(when) if when else email.utils.formatdate(localtime=True)
    return (
        f"{source} ({version}) {distribution}; urgency=medium\n\n"
        f"  * {note}\n\n"
        f" -- {maintainer}  {date_text}\n\n"
    )


def rules_list_missing(text):
    if re.search(r"(?m)^override_dh_missing:", text):
        return re.sub(r"--fail-missing", "--list-missing", text)
    return text.rstrip("\n") + "\n\noverride_dh_missing:\n\tdh_missing --list-missing\n"


def series_names(text):
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line.split()[0])
    return names


def series_entries(text):
    entries = []
    for line in text.splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        parts = body.split()
        strip = 1
        for index, part in enumerate(parts[1:], 1):
            match = re.match(r"^-p(\d+)$", part)
            if match:
                strip = int(match.group(1))
            elif part == "-p" and index + 1 < len(parts) and parts[index + 1].isdigit():
                strip = int(parts[index + 1])
        entries.append((parts[0], strip, body))
    return entries


def check_configure_line(line, canonical):
    return [flag for flag in canonical.values() if flag not in line]


def parse_unbound_version_output(text):
    info = {"configure": "", "modules": ""}
    for line in text.splitlines():
        if line.startswith("Configure line:"):
            info["configure"] = line.split(":", 1)[1].strip()
        elif line.startswith("Linked modules:"):
            mods = line.split(":", 1)[1]
            mods = re.sub(r"\[[^\]]*\]", "", mods)
            info["modules"] = " ".join(mods.split())
        elif line.startswith("Version "):
            info["version"] = line.split()[1]
    return info
