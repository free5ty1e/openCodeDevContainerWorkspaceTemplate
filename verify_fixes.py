#!/usr/bin/env python3
"""
Verification script to check that all fixes are working correctly.
Run this after setting up the environment to confirm everything works.
"""

import os
import sys
import subprocess
import json
import shutil

def run_cmd(cmd, capture=True):
    """Run a command and return result."""
    try:
        if capture:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
        else:
            result = subprocess.run(cmd, shell=True, timeout=30)
            return result.returncode == 0, "", ""
    except subprocess.TimeoutExpired:
        return False, "", "Timeout"
    except Exception as e:
        return False, "", str(e)

def check_statusline():
    """Check if statusline.sh is properly set up."""
    print("🔍 Checking statusline.sh...")

    # Check if symlink exists and is correct
    statusline_path = os.path.expanduser("~/.claude/statusline.sh")
    if not os.path.islink(statusline_path):
        print("  ❌ ~/.claude/statusline.sh is not a symlink")
        return False

    target = os.readlink(statusline_path)
    expected = "/workspace/statusline.sh"
    if target != expected:
        print(f"  ❌ Symlink points to {target}, expected {expected}")
        return False

    # Check if target exists and is executable
    if not os.path.isfile(expected):
        print(f"  ❌ Target {expected} does not exist")
        return False

    if not os.access(expected, os.X_OK):
        print(f"  ❌ Target {expected} is not executable")
        return False

    print("  ✅ statusline.sh symlink is correct and executable")
    return True

def check_persistence():
    """Check if Claude persistence is set up correctly."""
    print("🔍 Checking Claude persistence...")

    claude_path = os.path.expanduser("~/.claude")
    persist_path = os.path.join(os.getcwd(), ".claude_persist")

    # Check if ~/.claude is a symlink to .claude_persist
    if not os.path.islink(claude_path):
      print("  ❌ ~/.claude is not a symlink")
      return False

    target = os.readlink(claude_path)
    if target != persist_path:
        print(f"  ❌ ~/.claude points to {target}, expected {persist_path}")
        return False

    # Check if persist directory exists
    if not os.path.isdir(persist_path):
        print(f"  ❌ Persist directory {persist_path} does not exist")
        return False

    print("  ✅ Claude persistence is correctly set up")
    return True

def check_litellm():
    """Check if litellm is available."""
    print("🔍 Checking litellm availability...")

    # Check multiple possible locations for litellm
    possible_paths = [
        shutil.which("litellm"),  # From PATH
        "/workspace/.venv/bin/litellm",  # From venv
        os.path.expanduser("~/.local/bin/litellm"),  # From user install
    ]

    found = False
    for path in possible_paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            # Check if it works
            success, stdout, stderr = run_cmd(f"{path} --version")
            if success:
                print(f"  ✅ litellm available at {path}: {stdout}")
                found = True
                break

    if not found:
        print("  ❌ litellm command not found or not working in any location")
        return False

    return True

def check_claude_cli():
    """Check if claude CLI is available."""
    print("🔍 Checking claude CLI availability...")

    success, stdout, stderr = run_cmd("which claude")
    if not success:
        print("  ❌ claude command not found in PATH")
        return False

    success, stdout, stderr = run_cmd("claude --version")
    if not success:
        print("  ❌ claude command failed to run")
        return False

    if "error" in stdout.lower() or "not installed" in stdout.lower():
        print(f"  ❌ claude version check failed: {stdout}")
        return False

    print(f"  ✅ claude CLI available: {stdout.strip()}")
    return True

def main():
    """Run all verification checks."""
    print("=" * 60)
    print("  NVIDIA → Claude Code Bridge - Fix Verification")
    print("=" * 60)
    print()

    checks = [
        ("Statusline Symlink", check_statusline),
        ("Claude Persistence", check_persistence),
        ("Litellm Availability", check_litellm),
        ("Claude CLI Availability", check_claude_cli),
    ]

    passed = 0
    total = len(checks)

    for name, check_func in checks:
        print(f"{name}:")
        if check_func():
            passed += 1
        print()

    print("=" * 60)
    print(f"Results: {passed}/{total} checks passed")

    if passed == total:
        print("🎉 All fixes are working correctly!")
        print("✅ Ready to run: python3 claude_nvidia.py --dangerously-skip-permissions")
        return 0
    else:
        print("❌ Some fixes need attention")
        return 1

if __name__ == "__main__":
    sys.exit(main())