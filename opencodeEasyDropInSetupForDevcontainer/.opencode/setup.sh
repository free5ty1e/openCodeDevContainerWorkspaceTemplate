#!/bin/bash
set -e

OPENCODE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${OPENCODE_HOME}/opencode.json"
SOURCE_LINE="source \"${OPENCODE_HOME}/.env\""

echo "🚀 Setting up OpenCode for workspace-local storage..."

# Ensure .opencode directory exists
mkdir -p "${OPENCODE_HOME}"
echo "✓ Ensured .opencode directory exists"

# Check if opencode is installed
if ! command -v opencode &> /dev/null; then
    echo "📦 OpenCode not found, installing globally..."
    npm install -g opencode-ai
    echo "✓ OpenCode installed"
else
    echo "✓ OpenCode already installed"
fi

# Ensure config file exists
if [ ! -f "${CONFIG_FILE}" ]; then
    echo "⚠️  opencode.json not found at ${CONFIG_FILE}"
    echo "   Please ensure your opencode.json config is in place"
    exit 1
fi
echo "✓ Config file found at ${CONFIG_FILE}"

# Set OPENCODE_HOME environment variable for current session
export OPENCODE_HOME="${OPENCODE_HOME}"
echo "✓ Set OPENCODE_HOME=${OPENCODE_HOME}"

# Create .opencode subdirectories for storage
mkdir -p "${OPENCODE_HOME}/db"
mkdir -p "${OPENCODE_HOME}/cache"
echo "✓ Created storage directories"

# Create a .env file for persistent configuration across shell sessions
cat > "${OPENCODE_HOME}/.env" << EOF
# OpenCode workspace-local configuration
# Source this file in your shell profile to persist settings across sessions
export OPENCODE_HOME="${OPENCODE_HOME}"
export OPENCODE_CONFIG="${CONFIG_FILE}"
EOF
echo "✓ Created .opencode/.env for persistent configuration"

# Detect shell and add sourcing to profile if not already present
SHELL_NAME="$(basename "${SHELL}")"
case "${SHELL_NAME}" in
  zsh)
    PROFILE="${HOME}/.zshrc"
    ;;
  fish)
    PROFILE="${HOME}/.config/fish/config.fish"
    ;;
  *)
    # Default to bashrc for bash and others
    PROFILE="${HOME}/.bashrc"
    ;;
esac

if [ -f "${PROFILE}" ]; then
  if ! grep -qF "${SOURCE_LINE}" "${PROFILE}"; then
    echo "" >> "${PROFILE}"
    echo "# OpenCode workspace-local configuration" >> "${PROFILE}"
    echo "${SOURCE_LINE}" >> "${PROFILE}"
    echo "✓ Added OpenCode environment to ${PROFILE}"
  else
    echo "✓ OpenCode environment already configured in ${PROFILE}"
  fi
else
  echo "⚠️  Shell profile not found at ${PROFILE}"
  echo "   Please manually add this line to your shell profile:"
  echo "   ${SOURCE_LINE}"
fi

echo ""
echo "🧪 Validating OpenCode setup..."
echo ""

# Test 1: Verify OpenCode version
if command -v opencode &> /dev/null; then
  VERSION=$(opencode --version 2>&1 || echo "unknown")
  echo "✓ OpenCode version: ${VERSION}"
else
  echo "✗ OpenCode command not found"
  exit 1
fi

# Test 2: Verify OPENCODE_HOME is set correctly
if [ -z "${OPENCODE_HOME}" ]; then
  echo "✗ OPENCODE_HOME not set"
  exit 1
fi
echo "✓ OPENCODE_HOME=${OPENCODE_HOME}"

# Test 3: Verify storage directories exist
if [ -d "${OPENCODE_HOME}/db" ] && [ -d "${OPENCODE_HOME}/cache" ]; then
  echo "✓ Storage directories created"
else
  echo "✗ Storage directories not found"
  exit 1
fi

# Test 4: Verify .env file exists and has correct content
if grep -q "OPENCODE_HOME" "${OPENCODE_HOME}/.env"; then
  echo "✓ Configuration file (.env) is valid"
else
  echo "✗ Configuration file (.env) is invalid"
  exit 1
fi

# Test 5: Verify opencode.json exists and is valid JSON
if [ -f "${CONFIG_FILE}" ]; then
  if command -v jq &> /dev/null; then
    if jq empty "${CONFIG_FILE}" 2>/dev/null; then
      echo "✓ opencode.json is valid"
    else
      echo "⚠️  opencode.json exists but may have syntax issues"
    fi
  else
    echo "✓ opencode.json exists (JSON validation skipped, jq not installed)"
  fi
else
  echo "✗ opencode.json not found"
  exit 1
fi

echo ""
echo "✅ OpenCode setup complete and validated!"
echo ""
echo "📝 To use OpenCode with workspace-local storage in your current session:"
echo "   export OPENCODE_HOME=\"${OPENCODE_HOME}\""
echo ""
echo "🎯 Running OpenCode:"
echo "   opencode"
