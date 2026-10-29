#!/usr/bin/env bash
# Build a branded macOS client on this Mac, following the macOS job of custom-build.yml.
#
#     custom/build-mac.sh uneq kunde
#
# Builds the committed HEAD (not uncommitted changes) for the Mac's own architecture.
# Tools and build trees live in $NR_BUILD_DIR (default: netcotel-build next to the repo);
# the dmg ends up in $NR_BUILD_DIR/out/. Needs Xcode and Homebrew with
# llvm, create-dmg, pkg-config, cmake, ninja and cocoapods.
set -euo pipefail

BRAND=${1:?usage: build-mac.sh <brand> <kunde|admin>}
VARIANT=${2:?usage: build-mac.sh <brand> <kunde|admin>}
REPO=$(cd "$(dirname "$0")/.." && pwd)
B=${NR_BUILD_DIR:-$(dirname "$REPO")/netcotel-build}

# Same versions as CI, so a local build and a CI build come from the same toolchain.
wf_env() { sed -n "s/^  $1: \"\([^\"]*\)\".*/\1/p" "$REPO/.github/workflows/$2" | head -n1; }
FLUTTER_VERSION=$(wf_env FLUTTER_VERSION custom-build.yml)
RUST_VERSION=$(wf_env MAC_RUST_VERSION custom-build.yml)
VCPKG_COMMIT=$(wf_env VCPKG_COMMIT_ID custom-build.yml)
FRB_VERSION=$(wf_env FLUTTER_RUST_BRIDGE_VERSION bridge.yml)
CARGO_EXPAND_VERSION=$(wf_env CARGO_EXPAND_VERSION bridge.yml)

# Use /Applications/Xcode.app even when xcode-select still points at the command line tools.
if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
if ! xcodebuild -version >/dev/null 2>&1; then
  echo "Xcode is missing or not selected. Install it from the App Store, then run:" >&2
  echo "  sudo xcode-select -s /Applications/Xcode.app/Contents/Developer" >&2
  exit 1
fi

mkdir -p "$B/bin" "$B/out" "$B/src"
export PATH="$B/bin:$B/flutter/bin:$HOME/.cargo/bin:$(brew --prefix)/bin:$PATH"
export RUSTUP_TOOLCHAIN=$RUST_VERSION
export VCPKG_ROOT=$B/vcpkg

if ! command -v nasm >/dev/null; then
  # NASM 3.x (Homebrew) has an incompatible CLI; the multimedia ports need 2.x.
  curl -sSfL -o "$B/nasm.zip" https://www.nasm.us/pub/nasm/releasebuilds/2.16.03/macosx/nasm-2.16.03-macosx.zip
  unzip -qo "$B/nasm.zip" -d "$B" && cp "$B/nasm-2.16.03/nasm" "$B/bin/"
fi

if [ ! -d "$B/flutter" ]; then
  git clone -q --depth 1 -b "$FLUTTER_VERSION" https://github.com/flutter/flutter.git "$B/flutter"
fi
if [ ! -f "$B/flutter/.nr-patched" ]; then
  if [ "$FLUTTER_VERSION" = "3.24.5" ]; then
    git -C "$B/flutter" apply "$REPO/.github/patches/flutter_3.24.4_dropdown_menu_enableFilter.diff"
  fi
  # https://github.com/flutter/flutter/issues/133533
  sed -i '' -e 's/_setFramesEnabledState(false);/\/\/_setFramesEnabledState(false);/g' \
    "$B/flutter/packages/flutter/lib/src/scheduler/binding.dart"
  touch "$B/flutter/.nr-patched"
  flutter config --no-analytics >/dev/null
fi

rustup toolchain install "$RUST_VERSION" --profile minimal -c rustfmt
command -v cargo-expand >/dev/null || cargo install cargo-expand --version "$CARGO_EXPAND_VERSION" --locked
command -v flutter_rust_bridge_codegen >/dev/null ||
  cargo install flutter_rust_bridge_codegen --version "$FRB_VERSION" --features uuid --locked

if [ ! -d "$VCPKG_ROOT" ]; then
  git clone -q https://github.com/microsoft/vcpkg.git "$VCPKG_ROOT"
fi
if [ "$(git -C "$VCPKG_ROOT" rev-parse HEAD)" != "$VCPKG_COMMIT" ]; then
  git -C "$VCPKG_ROOT" fetch -q origin && git -C "$VCPKG_ROOT" checkout -q "$VCPKG_COMMIT"
  "$VCPKG_ROOT/bootstrap-vcpkg.sh" -disableMetrics
fi

# A fresh worktree per build, because brand.py rewrites the sources it brands.
SRC=$B/src/$BRAND-$VARIANT
if [ -e "$SRC" ]; then
  git -C "$REPO" worktree remove --force "$SRC"
fi
git -C "$REPO" worktree add -q --detach "$SRC" HEAD
cd "$SRC"
git submodule update -q --init --recursive

python3 custom/brand.py --brand "$BRAND" --variant "$VARIANT"
{ read -r APP; read -r DISPLAY; read -r PREFIX; } < <(python3 -c 'import json, sys
b = json.load(open(sys.argv[1]))
print(b["app_name"], b["display_name"], b["file_prefix"], sep="\n")' "custom/brands/$BRAND/brand.json")
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' Cargo.toml | head -n1)

"$VCPKG_ROOT/vcpkg" install --x-install-root="$VCPKG_ROOT/installed"

(cd flutter && flutter pub get)
flutter_rust_bridge_codegen --rust-input ./src/flutter_ffi.rs \
  --dart-output ./flutter/lib/generated_bridge.dart --c-output ./flutter/macos/Runner/bridge_generated.h
cp ./flutter/macos/Runner/bridge_generated.h ./flutter/ios/Runner/bridge_generated.h

ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
  MIN_MACOS_VERSION="12.3"
  sed -i '' -e "s/MACOSX_DEPLOYMENT_TARGET\=[0-9]*.[0-9]*/MACOSX_DEPLOYMENT_TARGET=${MIN_MACOS_VERSION}/" build.py
  sed -i '' -e "s/platform :osx, '.*'/platform :osx, '${MIN_MACOS_VERSION}'/" flutter/macos/Podfile
  sed -i '' -e "s/osx_minimum_system_version = \"[0-9]*.[0-9]*\"/osx_minimum_system_version = \"${MIN_MACOS_VERSION}\"/" Cargo.toml
  sed -i '' -e "s/MACOSX_DEPLOYMENT_TARGET = [0-9]*.[0-9]*;/MACOSX_DEPLOYMENT_TARGET = ${MIN_MACOS_VERSION};/" flutter/macos/Runner.xcodeproj/project.pbxproj
fi
./build.py --flutter --hwcodec --unix-file-copy-paste

# Without an Apple certificate the app is signed ad hoc. build.py copies the service
# binary into the finished bundle, which breaks the seal Xcode made, so sign again.
APP_PATH="./flutter/build/macos/Build/Products/Release/$APP.app"
codesign --force --deep --sign - --entitlements ./flutter/macos/Runner/Release.entitlements "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
[ "$ARCH" = "arm64" ] && DMG_ARCH=aarch64 || DMG_ARCH=x86_64
DMG="$B/out/$PREFIX-$VARIANT-$VERSION-$DMG_ARCH.dmg"
rm -f "$DMG"
create-dmg --volname "$DISPLAY" --icon "$APP.app" 200 190 --hide-extension "$APP.app" \
  --window-size 800 400 --app-drop-link 600 185 "$DMG" "$APP_PATH"
echo "built $DMG"
