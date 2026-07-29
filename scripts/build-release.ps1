[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonCommand = $Python
if (Test-Path -LiteralPath $Python) {
    $pythonCommand = (Resolve-Path -LiteralPath $Python).Path
}
$distPath = Join-Path $repoRoot "dist"
$buildPath = Join-Path $repoRoot "build"
$eggInfoPath = Join-Path $repoRoot "cgl_interagentmail.egg-info"

if (-not $SkipDependencyInstall) {
    & $pythonCommand -m pip install --upgrade "build>=1.2,<2" "twine>=6,<7"
    if ($LASTEXITCODE -ne 0) { throw "Could not install release tooling." }
}

& $pythonCommand -m unittest discover -s (Join-Path $repoRoot "tests") -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }

foreach ($target in @($distPath, $buildPath, $eggInfoPath)) {
    $resolvedParent = (Resolve-Path (Split-Path $target -Parent)).Path
    if ($resolvedParent -ne $repoRoot) {
        throw "Refusing to clean a release path outside the repository: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $distPath | Out-Null
Push-Location (Split-Path $repoRoot -Parent)
try {
    & $pythonCommand -m build $repoRoot --outdir $distPath
    if ($LASTEXITCODE -ne 0) { throw "Package build failed." }
}
finally {
    Pop-Location
}

$packages = Get-ChildItem -LiteralPath $distPath -File | Where-Object { $_.Extension -eq ".whl" -or $_.Name.EndsWith(".tar.gz") }
if ($packages.Count -ne 2) {
    throw "Expected exactly one wheel and one source archive; found $($packages.Count)."
}

& $pythonCommand -m twine check @($packages.FullName)
if ($LASTEXITCODE -ne 0) { throw "Twine package validation failed." }

$forbidden = "(^|/)(mailboxes|chats|run|DEV|InterAgentChatReader|\.venv)(/|$)|(^|/)config\.json$"
foreach ($package in $packages) {
    if ($package.Extension -eq ".whl") {
        $listing = & $pythonCommand -m zipfile -l $package.FullName
    }
    else {
        $listing = & tar -tzf $package.FullName
    }
    $normalized = $listing -replace "\\", "/"
    if ($normalized | Select-String -Pattern $forbidden) {
        throw "Private or runtime content was found in $($package.Name)."
    }
    if (-not ($normalized | Select-String -Pattern "(^|/)LICENSE(\s|$)")) {
        throw "The MIT LICENSE file is missing from $($package.Name)."
    }
    $normalized | Write-Host
}

$checksumPath = Join-Path $distPath "SHA256SUMS.txt"
$checksumLines = foreach ($package in ($packages | Sort-Object Name)) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $package.FullName).Hash.ToLowerInvariant()
    "$hash  $($package.Name)"
}
$checksumLines | Set-Content -LiteralPath $checksumPath -Encoding ascii

Write-Host "Release artifacts:"
Get-ChildItem -LiteralPath $distPath -File | Select-Object Name, Length
Write-Host "Checksums:"
Get-Content -LiteralPath $checksumPath
