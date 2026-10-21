# Sign one file with the owner's Azure Artifact Signing certificate.
#
# PLAN-0011 phase 1, prescription 1. The bundler calls this with one file
# path, once for each file it signs: the main executable, each NSIS
# plugin DLL, the uninstaller (through makensis) and the installer.
#
# Nothing here is a credential and nothing here reads one. The three
# fields in signing.json name an endpoint, an account and a certificate
# profile. Who may use that profile is decided by the Azure login already
# on the machine, which the Microsoft client library reads for itself.
#
# Windows PowerShell 5.1 syntax throughout: no && or ||, no ternary.
# Run it alone the way the bundler runs it, from app/src-tauri:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File installer/sign.ps1 <file>
#
# A refusal writes one sentence to standard error and exits 1, which
# fails the build. The bundler pipes both streams and prints them at
# debug level, so `cargo tauri build --bundles nsis -v` is what shows the
# sentence when a build stops here.

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $Path
)

# Continue, not Stop, on purpose. Windows PowerShell 5.1 turns a native
# program's standard error into a terminating error under Stop whenever
# the caller has redirected the streams, and the bundler redirects both.
# Every program below is checked through $LASTEXITCODE instead, so a
# failure still stops the build, and it stops with the one sentence this
# script meant to write rather than a stack trace.
$ErrorActionPreference = "Continue"

function Stop-Here([string] $sentence) {
    [Console]::Error.WriteLine($sentence)
    exit 1
}

# SignTool, from the Windows SDK. Every version directory under bin/ is
# read and the highest one wins, so a machine that carries three SDKs
# signs with the newest of them rather than with whichever sorts first
# as text.
$programs = ${env:ProgramFiles(x86)}
if (-not $programs) {
    $programs = "C:\Program Files (x86)"
}
$kits = Join-Path $programs "Windows Kits\10\bin"
$signtool = ""
if (Test-Path -LiteralPath $kits) {
    $versions = @(Get-ChildItem -LiteralPath $kits -Directory |
        Where-Object { $_.Name -match "^\d+(\.\d+){3}$" } |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "x64\signtool.exe") } |
        Sort-Object { [version] $_.Name })
    if ($versions.Count -gt 0) {
        $signtool = Join-Path $versions[-1].FullName "x64\signtool.exe"
    }
}
if (-not $signtool) {
    Stop-Here "No signtool.exe under ${kits}: install the Windows SDK signing tools, which put it at Windows Kits\10\bin\<version>\x64\signtool.exe."
}

# The Microsoft client library SignTool loads with /dlib. It installs per
# user, so this path is under the profile that runs the build.
$dlib = Join-Path $env:LOCALAPPDATA "Microsoft\MicrosoftArtifactSigningClientTools\Azure.CodeSigning.Dlib.dll"
if (-not (Test-Path -LiteralPath $dlib)) {
    Stop-Here "No Azure.CodeSigning.Dlib.dll at ${dlib}: install it with 'winget install -e --id Microsoft.Azure.ArtifactSigningClientTools'."
}

# The metadata, beside this script. It names the endpoint, the account
# and the profile, and nothing else.
$metadata = Join-Path $PSScriptRoot "signing.json"
if (-not (Test-Path -LiteralPath $metadata)) {
    Stop-Here "No signing.json beside this script at ${metadata}: it holds Endpoint, CodeSigningAccountName and CertificateProfileName, and nothing else."
}

# The login the library signs through. Read only, and asked before
# SignTool starts, so an expired login says so here instead of failing
# four minutes into a bundle.
$az = Get-Command az -ErrorAction SilentlyContinue
if (-not $az) {
    Stop-Here "No Azure CLI on this machine: install it and run 'az login' as an account that holds the Artifact Signing Certificate Profile Signer role."
}
$null = & az account show 2>&1
if ($LASTEXITCODE -ne 0) {
    Stop-Here "The Azure CLI holds no login: run 'az login' as an account that holds the Artifact Signing Certificate Profile Signer role."
}

# SHA256 both ways, and the Microsoft timestamp server. The certificate
# lives three days; the timestamp is what keeps the signature valid after
# it expires.
& $signtool sign /v /fd SHA256 /tr "http://timestamp.acs.microsoft.com" /td SHA256 /dlib $dlib /dmdf $metadata $Path
if ($LASTEXITCODE -ne 0) {
    Stop-Here "SignTool exited $LASTEXITCODE signing ${Path}: the lines above say why."
}

exit 0
