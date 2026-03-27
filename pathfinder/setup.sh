#!/usr/bin/env bash
# Pathfinder — one-time setup script
# Run from the repo root: bash pathfinder/setup.sh

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "========================================="
echo "  Pathfinder — Setup"
echo "========================================="
echo ""

# ── 1. Check Python ──────────────────────────────────────────────────────────
echo -n "Checking Python... "
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    echo -e "${GREEN}found Python ${PY_VERSION}${NC}"
elif command -v python &>/dev/null; then
    PY_VERSION=$(python --version 2>&1 | awk '{print $2}')
    echo -e "${GREEN}found Python ${PY_VERSION}${NC}"
else
    echo -e "${RED}Python 3 not found.${NC}"
    echo ""
    echo "  Install Python 3.10 or newer, then re-run this script."
    echo ""
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "  macOS:"
        echo "    brew install python3"
        echo "    — or download from https://www.python.org/downloads/"
    elif [[ "$OSTYPE" == "linux"* ]]; then
        echo "  Ubuntu / Debian:"
        echo "    sudo apt update && sudo apt install python3 python3-venv python3-pip"
        echo "  Fedora / RHEL:"
        echo "    sudo dnf install python3"
    else
        echo "  Windows:"
        echo "    Download from https://www.python.org/downloads/"
        echo "    Check 'Add Python to PATH' during install."
        echo "    Then run this script in Git Bash."
    fi
    echo ""
    exit 1
fi

# ── 2. Virtual environment ────────────────────────────────────────────────────
# Determine correct python command
PY_CMD=$(command -v python3 2>/dev/null || command -v python)

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    $PY_CMD -m venv .venv
    echo -e "${GREEN}Created .venv${NC}"
else
    echo -e "${GREEN}Virtual environment already exists${NC}"
fi

# Activate
if [[ "$OSTYPE" == "msys"* || "$OSTYPE" == "cygwin"* || "$OSTYPE" == "win32"* ]]; then
    source .venv/Scripts/activate 2>/dev/null || source .venv/Scripts/Activate.ps1 2>/dev/null || true
else
    source .venv/bin/activate
fi
echo -e "${GREEN}Activated .venv${NC}"

# ── 3. Install dependencies ──────────────────────────────────────────────────
echo ""
echo "Installing dependencies..."
pip install --upgrade pip -q
pip install -r pathfinder/requirements.txt -q
echo -e "${GREEN}All packages installed${NC}"

# ── 4. Copy config files if missing ──────────────────────────────────────────
echo ""
echo "Setting up config files..."

# Main config (at repo root — always present, just needs editing)
echo -e "  ${GREEN}Ready:${NC} config.yaml — ${YELLOW}edit this with your profile and search preferences${NC}"

# .env (in pathfinder/)
if [ ! -f "pathfinder/.env" ]; then
    cp pathfinder/.env.example pathfinder/.env
    echo -e "  ${GREEN}Created${NC} pathfinder/.env — ${YELLOW}add your GROQ_API_KEY, Gmail credentials, and DIGEST_RECIPIENT${NC}"
else
    echo -e "  ${GREEN}Already exists:${NC} pathfinder/.env"
fi

# ── 5. Next steps ─────────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo -e "${GREEN}  Setup complete!${NC}"
echo "========================================="
echo ""
echo "Two files to fill in:"
echo ""
echo -e "  1. ${YELLOW}pathfinder/.env${NC}"
echo "     Add your Groq API key (free: https://console.groq.com)"
echo "     Add your Gmail app password (see README Part 2)"
echo ""
echo -e "  2. ${YELLOW}config.yaml${NC}"
echo "     Replace the example profile with your own background."
echo "     Update the scoring criteria and search queries for your target roles."
echo ""
echo "Then test it:"
echo ""
echo -e "  ${GREEN}python pathfinder.py --test${NC}"
echo ""
echo "Full setup guide: README.md"
echo ""
