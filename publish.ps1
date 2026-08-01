# Rebuild the binder and publish it to GitHub Pages.
#
#   .\publish.ps1 collectr-export.csv
#   .\publish.ps1 collectr-export.csv C:\Users\David\Downloads\pokemon-binder
#
# Set your name once per session (or put it in your PowerShell profile):
#   $env:BINDER_OWNER = "David"

param(
    [Parameter(Mandatory = $true)][string]$Csv,
    [string]$Repo = "."
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Csv)) { throw "no such file: $Csv" }
if (-not (Test-Path (Join-Path $Repo ".git"))) {
    throw "$Repo is not a git repo - clone it first, see README.md"
}

# Windows installs Python as 'python'; 'python3' is usually a Store stub that
# opens the Microsoft Store instead of running anything.
$py = $null
foreach ($cmd in @("python", "py", "python3")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        $v = & $cmd --version 2>&1
        if ($v -match "Python 3") { $py = $cmd; break }
    }
}
if (-not $py) { throw "no Python 3 found on PATH - install it from python.org" }

Write-Host "==> building with $py" -ForegroundColor Cyan
$out = Join-Path $Repo "index.html"
$buildArgs = @("build_binder.py", $Csv, "-o", $out)
if ($env:BINDER_OWNER) { $buildArgs += @("--owner", $env:BINDER_OWNER) }
& $py @buildArgs
if ($LASTEXITCODE -ne 0) { throw "build failed" }

Write-Host "==> publishing" -ForegroundColor Cyan
Push-Location $Repo
try {
    git add index.html
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "no changes since last publish"
        return
    }
    git commit -q -m "binder update $(Get-Date -Format yyyy-MM-dd)"
    git push -q
    if ($LASTEXITCODE -ne 0) { throw "push failed" }

    $remote = (git remote get-url origin).Trim()
    $slug = $remote -replace '^(git@github\.com:|https://github\.com/)', '' -replace '\.git$', ''
    $user, $name = $slug -split '/', 2
    Write-Host ""
    Write-Host "published: https://$user.github.io/$name/" -ForegroundColor Green
    Write-Host "(first deploy takes a minute or two to go live)"
}
finally {
    Pop-Location
}
