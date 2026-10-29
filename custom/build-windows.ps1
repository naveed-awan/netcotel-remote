<#
Build a branded Windows client (x64) on a Windows 10/11 PC, following the Windows job of
custom-build.yml.

    powershell -ExecutionPolicy Bypass -File custom\build-windows.ps1 -Brand uneq -Variant kunde

Builds the committed HEAD (not uncommitted changes). The first run installs Git, Python,
Rust, LLVM and the Visual Studio 2022 C++ build tools with winget (confirm the UAC prompts),
then Flutter and vcpkg into -BuildDir. A short -BuildDir avoids Windows path length limits.
The result is <BuildDir>\out\<prefix>-<variant>-<version>-x86_64.exe.
#>
param(
    [Parameter(Mandatory)][string]$Brand,
    [Parameter(Mandatory)][ValidateSet('kunde', 'admin')][string]$Variant,
    [string]$BuildDir = 'C:\nr-build'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# A plain function on purpose: arguments like -q or -e must reach the tool, not PowerShell.
function Run {
    $Exe, $Rest = $args
    & $Exe @Rest
    if ($LASTEXITCODE) { throw "$Exe $Rest failed with exit code $LASTEXITCODE" }
}

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Ensure-Winget([string]$Command, [string]$Id, [string[]]$Extra = @()) {
    if (Get-Command $Command -ErrorAction SilentlyContinue) { return }
    Write-Host "installing $Id"
    Run winget install --id $Id -e --accept-package-agreements --accept-source-agreements @Extra
    Refresh-Path
}

$Repo = (Resolve-Path "$PSScriptRoot\..").Path
$B = $BuildDir
$Workflow = "$Repo\.github\workflows\custom-build.yml"
$BridgeWorkflow = "$Repo\.github\workflows\bridge.yml"

# Same versions as CI, so a local build and a CI build come from the same toolchain.
function Wf-Env([string]$Name, [string]$File) {
    $m = Select-String -Path $File -Pattern "^  ${Name}: `"([^`"]*)`"" | Select-Object -First 1
    if (-not $m) { throw "$Name not found in $File" }
    $m.Matches[0].Groups[1].Value
}
$FlutterVersion = Wf-Env FLUTTER_VERSION $Workflow
$RustVersion = Wf-Env SCITER_RUST_VERSION $Workflow
$LlvmVersion = Wf-Env LLVM_VERSION $Workflow
$VcpkgCommit = Wf-Env VCPKG_COMMIT_ID $Workflow
$FrbVersion = Wf-Env FLUTTER_RUST_BRIDGE_VERSION $BridgeWorkflow
$CargoExpandVersion = Wf-Env CARGO_EXPAND_VERSION $BridgeWorkflow
$Triplet = 'x64-windows-static'

$DevMode = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock' -ErrorAction SilentlyContinue
if ($DevMode.AllowDevelopmentWithoutDevLicense -ne 1) {
    # flutter build windows needs symlinks for its plugins
    throw 'Turn on Developer Mode first: Settings > System > For developers > Developer Mode'
}
New-Item -ItemType Directory -Force "$B\out", "$B\src", "$B\dl" | Out-Null

Ensure-Winget git Git.Git
# `py`, because `python` may be the Microsoft Store stub that only opens the Store.
Ensure-Winget py Python.Python.3.12
Ensure-Winget rustup Rustlang.Rustup
if (-not (Test-Path "$env:ProgramFiles\LLVM\bin\libclang.dll")) {
    Run winget install --id LLVM.LLVM -e --version $LlvmVersion --accept-package-agreements --accept-source-agreements
}
$VsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$HasVc = (Test-Path $VsWhere) -and
    (& $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath)
if (-not $HasVc) {
    Run winget install --id Microsoft.VisualStudio.2022.BuildTools -e --accept-package-agreements --accept-source-agreements `
        --override '--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
}
Refresh-Path

$env:LIBCLANG_PATH = "$env:ProgramFiles\LLVM\bin"
$env:Path = "$B\flutter\bin;$env:USERPROFILE\.cargo\bin;$env:ProgramFiles\LLVM\bin;$env:Path"
$env:RUSTUP_TOOLCHAIN = $RustVersion
$env:VCPKG_ROOT = "$B\vcpkg"
$env:VCPKG_DEFAULT_HOST_TRIPLET = $Triplet

Run rustup toolchain install $RustVersion --profile minimal -c rustfmt
if (-not (Get-Command cargo-expand -ErrorAction SilentlyContinue)) {
    Run cargo install cargo-expand --version $CargoExpandVersion --locked
}
if (-not (Get-Command flutter_rust_bridge_codegen -ErrorAction SilentlyContinue)) {
    Run cargo install flutter_rust_bridge_codegen --version $FrbVersion --features uuid --locked
}

if (-not (Test-Path "$B\flutter")) {
    Run git clone -q --depth 1 -b $FlutterVersion https://github.com/flutter/flutter.git "$B\flutter"
}
if (-not (Test-Path "$B\flutter\.nr-patched")) {
    Run flutter config --no-analytics
    Run flutter precache --windows
    # RustDesk ships its own build of the Windows engine.
    Invoke-WebRequest https://github.com/rustdesk/engine/releases/download/main/windows-x64-release.zip -OutFile "$B\dl\engine.zip"
    Expand-Archive -Force "$B\dl\engine.zip" "$B\dl\engine"
    Copy-Item -Force -Recurse "$B\dl\engine\*" "$B\flutter\bin\cache\artifacts\engine\windows-x64-release\"
    if ($FlutterVersion -eq '3.24.5') {
        Run git -C "$B\flutter" apply "$Repo\.github\patches\flutter_3.24.4_dropdown_menu_enableFilter.diff"
    }
    New-Item -ItemType File "$B\flutter\.nr-patched" | Out-Null
}

if (-not (Test-Path $env:VCPKG_ROOT)) {
    Run git clone -q https://github.com/microsoft/vcpkg.git $env:VCPKG_ROOT
}
if ((git -C $env:VCPKG_ROOT rev-parse HEAD) -ne $VcpkgCommit) {
    Run git -C $env:VCPKG_ROOT fetch -q origin
    Run git -C $env:VCPKG_ROOT checkout -q $VcpkgCommit
    Run "$env:VCPKG_ROOT\bootstrap-vcpkg.bat" -disableMetrics
}

$TopMost = "$B\topmost"
if (-not (Test-Path "$TopMost\WindowInjection\x64\Release\WindowInjection.dll")) {
    if (-not (Test-Path $TopMost)) {
        Run git clone -q https://github.com/rustdesk-org/RustDeskTempTopMostWindow $TopMost
    }
    Run git -C $TopMost checkout -q ecd8d6a139eee76845ea66423fb739af450fda90
    $MsBuild = & $VsWhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
    Run $MsBuild "$TopMost\WindowInjection\WindowInjection.vcxproj" '-p:Configuration=Release' '-p:Platform=x64' '/p:TargetVersion=Windows10'
}

# A fresh worktree per build, because brand.py rewrites the sources it brands.
$Src = "$B\src\$Brand-$Variant"
if (Test-Path $Src) { Run git -C $Repo worktree remove --force $Src }
Run git -C $Repo worktree add -q --detach $Src HEAD
Set-Location $Src
Run git submodule update -q --init --recursive

Run py -3 custom\brand.py --brand $Brand --variant $Variant
$BrandInfo = Get-Content "custom\brands\$Brand\brand.json" -Raw | ConvertFrom-Json
$Version = (Select-String -Path Cargo.toml -Pattern '^version = "(.*)"' | Select-Object -First 1).Matches[0].Groups[1].Value

Run "$env:VCPKG_ROOT\vcpkg.exe" install --triplet $Triplet "--x-install-root=$env:VCPKG_ROOT\installed"

Push-Location flutter
Run flutter pub get
Pop-Location
Run flutter_rust_bridge_codegen --rust-input .\src\flutter_ffi.rs `
    --dart-output .\flutter\lib\generated_bridge.dart --c-output .\flutter\macos\Runner\bridge_generated.h
Copy-Item .\flutter\macos\Runner\bridge_generated.h .\flutter\ios\Runner\bridge_generated.h

Run py -3 .\build.py --portable --flutter --skip-portable-pack --hwcodec --vram
Move-Item .\flutter\build\windows\x64\runner\Release .\rustdesk

Invoke-WebRequest https://github.com/rustdesk-org/rdev/releases/download/usbmmidd_v2/usbmmidd_v2.zip -OutFile usbmmidd_v2.zip
Expand-Archive usbmmidd_v2.zip -DestinationPath .
Remove-Item -Recurse usbmmidd_v2\Win32
Remove-Item usbmmidd_v2\deviceinstaller64.exe, usbmmidd_v2\deviceinstaller.exe, usbmmidd_v2\usbmmidd.bat
Move-Item usbmmidd_v2 .\rustdesk

try {
    $Base = 'https://github.com/rustdesk/hbb_common/releases/download/driver'
    Invoke-WebRequest "$Base/rustdesk_printer_driver_v4-1.4.zip" -OutFile rustdesk_printer_driver_v4-1.4.zip
    Invoke-WebRequest "$Base/printer_driver_adapter.zip" -OutFile printer_driver_adapter.zip
    Invoke-WebRequest "$Base/sha256sums" -OutFile sha256sums
    $SumDriver = (Select-String -Path .\sha256sums -Pattern '^([a-fA-F0-9]{64}) \*rustdesk_printer_driver_v4-1.4\.zip$').Matches[0].Groups[1].Value
    $SumAdapter = (Select-String -Path .\sha256sums -Pattern '^([a-fA-F0-9]{64}) \*printer_driver_adapter\.zip$').Matches[0].Groups[1].Value
    if ($SumDriver -eq (Get-FileHash rustdesk_printer_driver_v4-1.4.zip).Hash -and
        $SumAdapter -eq (Get-FileHash printer_driver_adapter.zip).Hash) {
        Expand-Archive rustdesk_printer_driver_v4-1.4.zip -DestinationPath .
        New-Item -ItemType Directory .\rustdesk\drivers | Out-Null
        Move-Item .\rustdesk_printer_driver_v4-1.4 .\rustdesk\drivers\RustDeskPrinterDriver
        Expand-Archive printer_driver_adapter.zip -DestinationPath .
        Move-Item printer_driver_adapter.dll .\rustdesk
    } else {
        Write-Host 'printer driver checksums do not match, building without it'
    }
} catch {
    Write-Host "printer driver skipped: $_"
}

$RunnerRes = Get-ChildItem -Recurse -Filter Runner.res -ErrorAction SilentlyContinue | Select-Object -First 1
if ($RunnerRes) { Copy-Item $RunnerRes.FullName .\libs\portable\Runner.res }
Copy-Item "$TopMost\WindowInjection\x64\Release\WindowInjection.dll" .\rustdesk

(Get-Content res\manifest.xml) | Where-Object { $_ -notmatch 'dpiAware' } | Set-Content res\manifest.xml
Push-Location libs\portable
Run py -3 -m pip install -r requirements.txt
Run py -3 .\generate.py -f ..\..\rustdesk\ -o . -e ..\..\rustdesk\rustdesk.exe
Pop-Location
$Exe = "$B\out\$($BrandInfo.file_prefix)-$Variant-$Version-x86_64.exe"
Move-Item -Force .\target\release\rustdesk-portable-packer.exe $Exe
Write-Host "built $Exe"
