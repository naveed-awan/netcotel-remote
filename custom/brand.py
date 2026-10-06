#!/usr/bin/env python3
"""Turn a RustDesk checkout into the branded client described by custom/brand.json.

Runs in CI right after checkout (see .github/workflows/custom-build.yml), so the
committed sources stay identical to upstream and merging upstream never conflicts.
Every edit asserts that the text it replaces is still there; if upstream changes
one of these spots the build fails here instead of producing a half-branded client.

    python3 custom/brand.py --variant kunde    # incoming connections only
    python3 custom/brand.py --variant admin    # full client
"""
import argparse
import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BRAND = json.loads((HERE / "brand.json").read_text())


def edit(rel, old, new, count=1, regex=False):
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    found = len(re.findall(old, text)) if regex else text.count(old)
    if found != count:
        raise SystemExit(f"{rel}: expected {count}x {old!r}, found {found}")
    text = re.sub(old, lambda _: new, text) if regex else text.replace(old, new)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"patched {rel}")


def rust_str(value):
    return json.dumps(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["kunde", "admin"], required=True)
    variant = parser.parse_args().variant

    app, display = BRAND["app_name"], BRAND["display_name"]
    if not re.fullmatch(r"[A-Za-z0-9]+", app):
        # the name is pasted unquoted into `sc create`, launchd labels and shell paths
        raise SystemExit("app_name must be alphanumeric without spaces")

    # App name, plus the built-in servers as a fallback behind the override below.
    cfg = "libs/hbb_common/src/config.rs"
    edit(cfg, 'pub static ref APP_NAME: RwLock<String> = RwLock::new("RustDesk".to_owned());',
         f"pub static ref APP_NAME: RwLock<String> = RwLock::new({rust_str(app)}.to_owned());")
    edit(cfg, r'pub const RENDEZVOUS_SERVERS: &\[&str\] = &\[[^\]]*\];',
         f"pub const RENDEZVOUS_SERVERS: &[&str] = &[{rust_str(BRAND['rendezvous_server'])}];", regex=True)
    edit(cfg, r'pub const RS_PUB_KEY: &str = "[^"]*";',
         f"pub const RS_PUB_KEY: &str = {rust_str(BRAND['server_key'])};", regex=True)

    # Fixed settings, applied in every process before anything reads the config.
    hard = ""
    if variant == "kunde":
        hard = ('    config::HARD_SETTINGS.write().unwrap()\n'
                '        .insert("conn-type".to_owned(), "incoming".to_owned());\n')
    common = "src/common.rs"
    edit(common, "pub fn load_custom_client() {\n",
         "pub fn load_custom_client() {\n    apply_builtin_client_config();\n")
    with open(ROOT / common, "a", encoding="utf-8", newline="") as f:
        f.write(
            "\n// Added by custom/brand.py\n"
            "fn apply_builtin_client_config() {\n"
            "    let mut settings = config::OVERWRITE_SETTINGS.write().unwrap();\n"
            f'    settings.insert("custom-rendezvous-server".to_owned(), {rust_str(BRAND["rendezvous_server"])}.to_owned());\n'
            f'    settings.insert("key".to_owned(), {rust_str(BRAND["server_key"])}.to_owned());\n'
            "    drop(settings);\n"
            f"{hard}"
            "}\n")

    # Windows version resources (shown in Explorer, Task Manager and the UAC prompt).
    rc = "flutter/windows/runner/Runner.rc"
    edit(rc, 'VALUE "FileDescription", "RustDesk Remote Desktop"', f'VALUE "FileDescription", "{display}"')
    edit(rc, 'VALUE "ProductName", "RustDesk"', f'VALUE "ProductName", "{display}"')
    packer = "libs/portable/Cargo.toml"
    edit(packer, 'ProductName = "RustDesk"', f'ProductName = "{display}"')
    edit(packer, 'FileDescription = "RustDesk Remote Desktop"', f'FileDescription = "{display}"')

    # macOS: the bundle and its executable must be named after the app, because the
    # daemon/agent plists are rewritten to /Applications/<app>.app/Contents/MacOS/<app>.
    edit("flutter/macos/Runner/Configs/AppInfo.xcconfig", "PRODUCT_NAME = RustDesk", f"PRODUCT_NAME = {app}")
    edit("flutter/macos/Runner.xcodeproj/project.pbxproj",
         "PRODUCT_BUNDLE_IDENTIFIER = com.carriez.rustdesk;",
         f"PRODUCT_BUNDLE_IDENTIFIER = {BRAND['bundle_id']};", count=3)
    edit("flutter/macos/Runner/Info.plist",
         "<key>CFBundleName</key>\n\t<string>$(PRODUCT_NAME)</string>",
         f"<key>CFBundleName</key>\n\t<string>{display}</string>")
    edit("build.py", "./build/macos/Build/Products/Release/RustDesk.app/Contents/MacOS/",
         f"./build/macos/Build/Products/Release/{app}.app/Contents/MacOS/")

    # Icons and logos.
    for src in sorted((HERE / "overlay").rglob("*")):
        if src.is_file():
            dst = ROOT / src.relative_to(HERE / "overlay")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            print(f"copied  {dst.relative_to(ROOT)}")

    print(f"branding applied: {display} ({variant})")


if __name__ == "__main__":
    main()
