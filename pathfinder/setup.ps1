# Pathfinder — one-time setup script for Windows (PowerShell)
# Run from the repo root: .\pathfinder\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================="
Write-Host "  Pathfinder — Setup"
Write-Host "========================================="
Write-Host ""

# ── 1. Check Python ───────────────────────────────────────────────────────────
Write-Host -NoNewline "Checking Python... "
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install Python 3.10 or newer from https://www.python.org/downloads/"
    Write-Host "  Check 'Add Python to PATH' during install, then re-run this script."
    Write-Host ""
    exit 1
}
$pyVersion = & python --version 2>&1
Write-Host "found $pyVersion" -ForegroundColor Green

# ── 2. Virtual environment ────────────────────────────────────────────────────
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    Write-Host "Created .venv" -ForegroundColor Green
} else {
    Write-Host "Virtual environment already exists" -ForegroundColor Green
}

# ── 3. Install dependencies ───────────────────────────────────────────────────
Write-Host ""
Write-Host "Installing dependencies..."
& .venv\Scripts\pip install --upgrade pip -q
& .venv\Scripts\pip install -r pathfinder\requirements.txt -q
Write-Host "All packages installed" -ForegroundColor Green

# ── 4. Copy config files if missing ──────────────────────────────────────────
Write-Host ""
Write-Host "Setting up config files..."

Write-Host "  Ready: config.yaml — " -NoNewline -ForegroundColor Green
Write-Host "edit this with your profile and search preferences" -ForegroundColor Yellow

if (-not (Test-Path "pathfinder\.env")) {
    Copy-Item pathfinder\.env.example pathfinder\.env
    Write-Host "  Created: pathfinder\.env — " -NoNewline -ForegroundColor Green
    Write-Host "add your GROQ_API_KEY, Gmail credentials, and DIGEST_RECIPIENT" -ForegroundColor Yellow
} else {
    Write-Host "  Already exists: pathfinder\.env" -ForegroundColor Green
}

# ── 5. Next steps ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================="
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "========================================="
Write-Host ""
Write-Host "Two files to fill in:"
Write-Host ""
Write-Host "  1. " -NoNewline
Write-Host "pathfinder\.env" -ForegroundColor Yellow
Write-Host "     Add your Groq API key (free: https://console.groq.com)"
Write-Host "     Add your Gmail app password (see README Part 2)"
Write-Host ""
Write-Host "  2. " -NoNewline
Write-Host "config.yaml" -ForegroundColor Yellow
Write-Host "     Replace the example profile with your own background."
Write-Host "     Update the scoring criteria and search queries for your target roles."
Write-Host ""
Write-Host "Activate the virtual environment before running Python commands:"
Write-Host ""
Write-Host "  .venv\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host ""
Write-Host "Then test it:"
Write-Host ""
Write-Host "  python pathfinder.py --test" -ForegroundColor Green
Write-Host ""
Write-Host "Full setup guide: README.md"
Write-Host ""
