#!/usr/bin/env python3
"""Turn a RustDesk checkout into the branded client described by custom/brands/<brand>/brand.json.

Runs in CI right after checkout (see .github/workflows/custom-build.yml), so the
committed sources stay identical to upstream and merging upstream never conflicts.
Every edit asserts that the text it replaces is still there; if upstream changes
one of these spots the build fails here instead of producing a half-branded client.

    python3 custom/brand.py --brand netcotel --variant kunde    # incoming connections only
    python3 custom/brand.py --brand uneq --variant admin        # full client
"""
import argparse
import json
import os
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BRANDS = sorted(p.name for p in (HERE / "brands").iterdir() if (p / "brand.json").is_file())


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
    parser.add_argument("--brand", choices=BRANDS, required=True)
    parser.add_argument("--variant", choices=["kunde", "admin"], required=True)
    args = parser.parse_args()
    variant, brand_dir = args.variant, HERE / "brands" / args.brand
    brand = json.loads((brand_dir / "brand.json").read_text())

    app, display = brand["app_name"], brand["display_name"]
    if not re.fullmatch(r"[A-Za-z0-9]+", app):
        # the name is pasted unquoted into `sc create`, launchd labels and shell paths
        raise SystemExit("app_name must be alphanumeric without spaces")

    # App name, plus the built-in servers as a fallback behind the override below.
    cfg = "libs/hbb_common/src/config.rs"
    edit(cfg, 'pub static ref APP_NAME: RwLock<String> = RwLock::new("RustDesk".to_owned());',
         f"pub static ref APP_NAME: RwLock<String> = RwLock::new({rust_str(app)}.to_owned());")
    edit(cfg, r'pub const RENDEZVOUS_SERVERS: &\[&str\] = &\[[^\]]*\];',
         f"pub const RENDEZVOUS_SERVERS: &[&str] = &[{rust_str(brand['rendezvous_server'])}];", regex=True)
    edit(cfg, r'pub const RS_PUB_KEY: &str = "[^"]*";',
         f"pub const RS_PUB_KEY: &str = {rust_str(brand['server_key'])};", regex=True)

    # Fixed settings, applied in every process before anything reads the config.
    # The address book, device groups and account login need the Pro API server.
    settings = {
        "OVERWRITE_SETTINGS": {
            "custom-rendezvous-server": brand["rendezvous_server"],
            "key": brand["server_key"],
        },
        "HARD_SETTINGS": {"disable-ab": "Y", "disable-account": "Y"},
        "BUILTIN_SETTINGS": {
            "hide-server-settings": "Y",
            "hide-websocket-settings": "Y",
            "hide-powered-by-me": "Y",
        },
    }
    if variant == "kunde":
        settings["HARD_SETTINGS"]["conn-type"] = "incoming"
        settings["BUILTIN_SETTINGS"]["hide-network-settings"] = "Y"
    inserts = "".join(
        f"    config::{target}.write().unwrap().insert({rust_str(k)}.to_owned(), {rust_str(v)}.to_owned());\n"
        for target, values in settings.items() for k, v in values.items())
    common = "src/common.rs"
    edit(common, "pub fn load_custom_client() {\n",
         "pub fn load_custom_client() {\n    apply_builtin_client_config();\n")
    with open(ROOT / common, "a", encoding="utf-8", newline="") as f:
        f.write(f"\n// Added by custom/brand.py\nfn apply_builtin_client_config() {{\n{inserts}}}\n")

    # Brand colors in place of the stock blue.
    accent, button = brand["accent_color"], brand["button_color"]
    dart = "flutter/lib/common.dart"
    edit(dart, "static const Color accent = Color(0xFF0071FF);", f"static const Color accent = Color(0xFF{accent});")
    edit(dart, "static const Color accent50 = Color(0x770071FF);", f"static const Color accent50 = Color(0x77{accent});")
    edit(dart, "static const Color accent80 = Color(0xAA0071FF);", f"static const Color accent80 = Color(0xAA{accent});")
    edit(dart, "static const Color idColor = Color(0xFF00B6F0);", f"static const Color idColor = Color(0xFF{button});")
    edit(dart, "static const Color button = Color(0xFF2C8CFF);", f"static const Color button = Color(0xFF{button});")
    edit(dart, "  return Color(0xFF2C8CFF);", f"  return Color(0xFF{button});")
    edit(dart, "primary: Colors.blue,", "primary: accent,", count=2)

    # Customer variant on macOS: ask for screen recording, then accessibility, on every
    # start until granted, and open the matching System Settings pane each time.
    if variant == "kunde":
        home = "flutter/lib/desktop/pages/desktop_home_page.dart"
        edit(home, "  @override\n  void initState() {\n",
             "  bool _nrAskedScreen = false;\n"
             "  bool _nrAskedAcc = false;\n"
             "\n"
             "  void _nrAutoPermissions() {\n"
             "    if (!isMacOS || bind.isOutgoingOnly()) return;\n"
             "    if (!bind.mainIsCanScreenRecording(prompt: false)) {\n"
             "      if (!_nrAskedScreen) {\n"
             "        _nrAskedScreen = true;\n"
             "        bind.mainIsCanScreenRecording(prompt: true);\n"
             "        launchUrl(Uri.parse(\n"
             "            'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'));\n"
             "        watchIsCanScreenRecording = true;\n"
             "      }\n"
             "      return;\n"
             "    }\n"
             "    if (!_nrAskedAcc && !bind.mainIsProcessTrusted(prompt: false)) {\n"
             "      _nrAskedAcc = true;\n"
             "      bind.mainIsProcessTrusted(prompt: true);\n"
             "      launchUrl(Uri.parse(\n"
             "          'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'));\n"
             "      watchIsProcessTrust = true;\n"
             "    }\n"
             "  }\n"
             "\n"
             "  @override\n  void initState() {\n")
        edit(home, "    _updateTimer = periodic_immediate(const Duration(seconds: 1), () async {\n",
             "    _updateTimer = periodic_immediate(const Duration(seconds: 1), () async {\n"
             "      _nrAutoPermissions();\n")

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
         f"PRODUCT_BUNDLE_IDENTIFIER = {brand['bundle_id']};", count=3)
    edit("flutter/macos/Runner/Info.plist",
         "<key>CFBundleName</key>\n\t<string>$(PRODUCT_NAME)</string>",
         f"<key>CFBundleName</key>\n\t<string>{display}</string>")
    edit("build.py", "./build/macos/Build/Products/Release/RustDesk.app/Contents/MacOS/",
         f"./build/macos/Build/Products/Release/{app}.app/Contents/MacOS/")

    # Icons and logos.
    for src in sorted((brand_dir / "overlay").rglob("*")):
        if src.is_file():
            dst = ROOT / src.relative_to(brand_dir / "overlay")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            print(f"copied  {dst.relative_to(ROOT)}")

    # Names for the packaging steps that follow in the same CI job.
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as f:
            f.write(f"BRAND_APP={app}\nBRAND_DISPLAY={display}\nBRAND_PREFIX={brand['file_prefix']}\n")

    print(f"branding applied: {display} ({variant})")


if __name__ == "__main__":
    main()
