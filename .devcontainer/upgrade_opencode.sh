#!/usr/bin/env bash
# =============================================================================
# upgrade_opencode.sh — Download and install the latest version of OpenCode
# =============================================================================
# This script fetches the latest release of opencode from GitHub and replaces
# the currently installed version. Use this when you want to manually upgrade
# without rebuilding the devcontainer.
#
# Usage: bash .devcontainer/upgrade_opencode.sh
# =============================================================================

set -euo pipefail

echo "=========================================="
echo "📥 OpenCode Upgrader"
echo "=========================================="
echo ""

# Check for required tools
if ! command -v curl &> /dev/null; then
    echo "❌ curl is required but not installed."
    exit 1
fi
if ! command -v jq &> /dev/null; then
    echo "❌ jq is required but not installed."
    exit 1
fi

# Show current version if installed
CURRENT_VERSION=""
if command -v opencode &> /dev/null; then
    CURRENT_VERSION=$(opencode --version 2>&1 || true)
    echo "Current: $CURRENT_VERSION"
else
    echo "Current: not installed"
fi

echo ""

# Fetch latest release tag from GitHub
echo "🔍 Checking latest release..."
LATEST_TAG=$(curl -fsSL https://api.github.com/repos/anomalyco/opencode/releases/latest | jq -r '.tag_name')
echo "   Latest: $LATEST_TAG"

# If already on latest, offer to reinstall anyway
if [[ "$(opencode --version 2>&1 || true)" == *"$LATEST_TAG"* ]]; then
    echo ""
    echo "⚠️  You already have $LATEST_TAG installed."
    echo "   Continuing will reinstall the same version."
fi

echo ""
echo "⬇️  Downloading opencode ${LATEST_TAG}..."
curl -fsSL -o /tmp/opencode.tar.gz "https://github.com/anomalyco/opencode/releases/download/${LATEST_TAG}/opencode-linux-x64.tar.gz"

echo "📦 Installing..."
sudo tar -xzf /tmp/opencode.tar.gz -C /usr/local/bin
sudo chmod +x /usr/local/bin/opencode
rm /tmp/opencode.tar.gz

echo ""
echo "✅ OpenCode upgraded to ${LATEST_TAG}!"
echo ""

# Verify installation
echo "Verifying..."
opencode --version

echo ""
echo "=========================================="
echo "✅ Upgrade complete!"
echo "=========================================="
