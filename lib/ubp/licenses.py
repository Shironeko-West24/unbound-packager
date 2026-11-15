import re

from .util import UbpError


def copyright_stanzas(text):
    stanzas = []
    for block in re.split(r"\n\s*\n", text):
        files = re.search(r"(?ms)^Files:\s*(.+?)(?=^\S|\Z)", block)
        license_line = re.search(r"(?m)^License:\s*(.+)$", block)
        if files and license_line:
            stanzas.append((files.group(1).split(), license_line.group(1).strip()))
    return stanzas


def all_licenses(text):
    return sorted({name for _, name in copyright_stanzas(text)})


def packaging_licenses(text):
    return sorted({name for files, name in copyright_stanzas(text) if any(f.startswith("debian/") for f in files)})


def check_packaging(text, allowed):
    found = packaging_licenses(text)
    if not found:
        raise UbpError("debian/copyright から、パッケージングのライセンスを読み取れませんでした。人の確認が必要です。")
    unexpected = [name for name in found if name not in allowed]
    if unexpected:
        raise UbpError(
            "パッケージングのライセンスが想定と違います: " + "、".join(unexpected)
            + "。公開してよい条件かを確かめ、packaging/options.json の allowed_packaging_licenses を見直してください。"
        )
    return found


def check_spec_license(value, allowed):
    if value not in allowed:
        raise UbpError(
            f"spec の License が想定と違います: {value}。公開してよい条件かを確かめ、packaging/options.json の allowed_spec_licenses を見直してください。"
        )
    return value


def replace_maintainer(control, maintainer):
    match = re.search(r"(?m)^Maintainer:\s*(.+)$", control)
    if not match:
        raise UbpError("debian/control に Maintainer がありません。")
    original = match.group(1).strip()
    if original == maintainer:
        return control
    new = f"Maintainer: {maintainer}"
    if not re.search(r"(?m)^XSBC-Original-Maintainer:", control):
        new += f"\nXSBC-Original-Maintainer: {original}"
    return control[: match.start()] + new + control[match.end():]
