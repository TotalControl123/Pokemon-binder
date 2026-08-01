# Pick up a fresh CollectR export and publish it.
#
#   .\refresh.ps1              # find the newest export in Downloads, publish
#   .\refresh.ps1 -Scrape      # open the showcase page and copy the scraper first
#   .\refresh.ps1 -Force       # publish even if the export shrank sharply
#
# Set these once (or add them to your PowerShell profile):
#   $env:BINDER_OWNER    = "David"
#   $env:BINDER_SHOWCASE = "https://app.getcollectr.com/showcase/profile/<your-id>"

param(
    [switch]$Scrape,
    [switch]$Force,
    [string]$Downloads = (Join-Path $HOME "Downloads")
)

$ErrorActionPreference = "Stop"
$target = "collectr-export.csv"

if (-not (Test-Path "build_binder.py")) {
    throw "run this from the repo folder (build_binder.py isn't here)"
}

# --- optionally kick off the scrape ------------------------------------------
if ($Scrape) {
    if (-not (Test-Path "collectr-export-v2.js")) {
        throw "collectr-export-v2.js is missing - can't copy the scraper"
    }
    Get-Content "collectr-export-v2.js" -Raw | Set-Clipboard
    if ($env:BINDER_SHOWCASE) { Start-Process $env:BINDER_SHOWCASE }
    else { Write-Host "set `$env:BINDER_SHOWCASE to open your page automatically" -ForegroundColor Yellow }

    Write-Host ""
    Write-Host "Scraper copied to your clipboard." -ForegroundColor Cyan
    Write-Host "  1. press F12, open the Console tab"
    Write-Host "  2. paste, press Enter"
    Write-Host "  3. run:  await collectrExport()"
    Write-Host "  4. wait for the download, then press Enter here"
    Read-Host "  ready"
}

# --- find the newest export --------------------------------------------------
# Windows names repeat downloads 'collectr-export (1).csv', so never assume the
# plain filename is the latest one.
$found = Get-ChildItem -Path $Downloads -Filter "collectr-export*.csv" -ErrorAction SilentlyContinue |
         Sort-Object LastWriteTime -Descending |
         Select-Object -First 1

if ($found) {
    $current = if (Test-Path $target) { (Get-Item $target).LastWriteTime } else { [datetime]::MinValue }
    if ($found.LastWriteTime -gt $current) {
        $age = [int]((Get-Date) - $found.LastWriteTime).TotalMinutes
        Write-Host "==> using $($found.Name) from Downloads ($age min old)" -ForegroundColor Cyan
        Move-Item $found.FullName $target -Force
    } else {
        Write-Host "==> Downloads has nothing newer; using the existing $target" -ForegroundColor Yellow
    }
} elseif (Test-Path $target) {
    Write-Host "==> no export found in Downloads; using the existing $target" -ForegroundColor Yellow
} else {
    throw "no $target here and nothing in $Downloads - run with -Scrape first"
}

# --- sanity-check the file before it goes anywhere ---------------------------
$head = Get-Content $target -TotalCount 1
foreach ($col in @("name", "set", "qty")) {
    if ($head -notmatch $col) { throw "$target doesn't look like a CollectR export (no '$col' column)" }
}
$lines = (Get-Content $target | Measure-Object -Line).Lines
Write-Host "    $lines rows, saved $((Get-Item $target).LastWriteTime)"

# --- build and publish -------------------------------------------------------
$args = @($target, ".")
if ($Force) { $env:BINDER_FORCE = "1" } else { Remove-Item Env:\BINDER_FORCE -ErrorAction SilentlyContinue }
& ".\publish.ps1" @args
