#!/usr/bin/env python3
"""
Claude Bridge Library - Shared functionality for all provider scripts.

This module contains common functions used across the Claude Code bridge scripts
for OpenCode Zen, Google, NVIDIA, and anyAPI. It provides:

- Prerequisite checking and installation (litellm, claude CLI)
- API key management
- Model fetching, categorizing, and selection
- Proxy management
- Model access testing
- Claude persistence and statusline setup

Usage:
    from claude_library import ensure_litellm, get_api_key, fetch_models, etc.
"""

import os
import sys
import subprocess
import json
import shutil
import time
import socket
import signal
import platform
import urllib.request
import urllib.error
import re

# ─── Configuration ─────────────────────────────────────────────────────────────

# Default paths
DEFAULT_VENV_PYTHON = "/workspace/.venv/bin/python3"

# The litellm install spec that pulls in ALL proxy runtime dependencies.
# `proxy` provides the console script + core proxy deps (fastapi, uvicorn, ...),
# while `extra-proxy` additionally provides `prisma`, which the proxy's DB
# error-handler imports even when `store_model_in_db` is disabled. A bare
# `litellm[proxy]` (or bare `litellm`) leaves `prisma` missing and crashes the
# proxy on any unauthenticated request. Use both extras together, always.
LITELLM_PROXY_SPEC = "litellm[proxy,extra-proxy]"


# ─── Native extension health / import diagnostics ──────────────────────────────
#
# A package can be *installed* yet still be unimportable. The usual cause in a
# container is a native extension (a compiled `.so`) built for a different CPU
# architecture than the one actually running -- e.g. a `.venv` baked on x86_64 that
# is then mounted into an aarch64 devcontainer. pip is happy (the dist-info says
# the requirement is satisfied) so `pip install <pkg>` is a silent no-op, and every
# import fails deep inside an unrelated dependency. Conflating that with "package
# missing" produces misleading messages and pointless reinstall loops.

_ELF_MACHINE = {
    "x86_64": 62, "amd64": 62,
    "aarch64": 183, "arm64": 183,
    "i686": 3, "i386": 3, "x86": 3,
    "armv7l": 40, "arm": 40,
    "ppc64le": 21, "ppc64": 21,
    "s390x": 22,
    "riscv64": 243,
    "loongarch64": 258,
}

# A dependency failing to import because one of ITS dependencies is broken.
IMPORT_OK = "ok"
IMPORT_NOT_INSTALLED = "not-installed"
IMPORT_BROKEN_NATIVE = "broken-native"
IMPORT_BROKEN = "broken"


def host_elf_machine():
    """Return the ELF e_machine value for this machine, or None if unknown."""
    import platform
    return _ELF_MACHINE.get(platform.machine().lower())


def _elf_machine_of(path):
    """Read e_machine from an ELF file's header. None if not ELF/unreadable."""
    try:
        with open(path, "rb") as f:
            header = f.read(20)
    except OSError:
        return None
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return None
    # EI_DATA: 1 = little endian, 2 = big endian. e_machine sits at 0x12.
    endian = "little" if header[5] == 1 else "big"
    return int.from_bytes(header[18:20], endian)


def _site_package_roots():
    """Best-effort list of site-packages / dist-packages directories on sys.path."""
    import site
    roots = []
    try:
        roots.extend(site.getsitepackages())
    except AttributeError:
        pass
    try:
        user_site = site.getusersitepackages()
        if isinstance(user_site, str):
            roots.append(user_site)
    except AttributeError:
        pass
    for entry in sys.path:
        if entry and entry not in roots and (
            entry.endswith("site-packages") or entry.endswith("dist-packages")
        ):
            roots.append(entry)
    return [r for r in roots if os.path.isdir(r)]


_DIST_FILE_MAP = None

# Native-extension architecture is scanned once per process: the scan walks every
# site-packages directory, and the answer cannot change within a run.
_NATIVE_SCAN_DONE = False


def _so_key(name):
    """Reduce a native-extension filename to a tag-independent key.

    ``orjson.cpython-312-x86_64-linux-gnu.so`` and
    ``orjson.cpython-312-aarch64-linux-gnu.so`` must map to the same key, otherwise a
    RECORD map built before a reinstall goes stale and the new binaries fall through
    to path guessing (which yields uninstallable names like ``yaml`` for ``PyYAML``).
    The module name is the part before the ABI/platform tags.
    """
    return name.split(".")[0]


def _dist_file_map():
    """Map normalized native-extension filenames -> owning distribution name.

    Deriving the distribution from the path is unreliable: wheels vendor their
    binaries into sibling directories (``numpy.libs/``, ``polars/_polars_runtime_32.so``)
    and some ship a bare top-level ``.so`` (``_cffi_backend...so``). RECORD is the
    authoritative answer and lets us hand pip a real distribution name.
    """
    global _DIST_FILE_MAP
    if _DIST_FILE_MAP is not None:
        return _DIST_FILE_MAP
    import importlib.metadata as md
    mapping = {}
    try:
        dists = list(md.distributions())
    except Exception:
        dists = []
    for dist in dists:
        try:
            name = dist.metadata["Name"]
        except Exception:
            name = None
        if not name:
            continue
        try:
            files = dist.files or []
        except Exception:
            continue
        for f in files:
            fn = os.path.basename(str(f))
            if ".so" not in fn:
                continue
            mapping.setdefault(_so_key(fn), name)
    _DIST_FILE_MAP = mapping
    return mapping


def _package_for_so(so_path):
    """Map a native extension path to the distribution that owns it.

    Returns a pip-installable distribution name, or None if it cannot be determined.
    """
    so_path = os.path.abspath(so_path)
    owner = _dist_file_map().get(_so_key(os.path.basename(so_path)))
    if owner:
        return owner
    return None


def find_broken_native_extensions():
    """Find installed native extensions built for a different CPU than this machine.

    Returns a list of dicts: {"package", "path", "reason"}.

    Detection is based solely on the ELF header's ``e_machine`` field, which is
    unambiguous and cannot produce false positives.

    An earlier version of this also tried to dlopen each matching ``.so`` to catch
    loader-level problems. That was wrong: a CPython extension module only exports
    ``PyInit_<its own name>``, so loading one under a synthetic probe name fails
    with "does not define module export function" for *every* correctly installed
    binary. That made a healthy environment look 100% broken and drove an endless
    reinstall loop. Loader-level breakage is now detected by importing the package
    itself (see ``diagnose_module``), which is the only honest test.
    """
    expected = host_elf_machine()
    if expected is None:
        return []
    found = []
    seen = set()
    for root in _site_package_roots():
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip caches; they only waste time and hold duplicate .so files.
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for fn in filenames:
                if ".so" not in fn:
                    continue
                so_path = os.path.join(dirpath, fn)
                if so_path in seen:
                    continue
                seen.add(so_path)
                machine = _elf_machine_of(so_path)
                if machine is None:
                    continue
                if expected is not None and machine != expected:
                    found.append({
                        "package": _package_for_so(so_path),
                        "path": so_path,
                        "reason": (
                            f"built for ELF machine {machine}, "
                            f"host is {expected}"
                        ),
                    })
    return found


def diagnose_module(module_name):
    """Import a module and classify the outcome.

    Returns (ok, status, detail). This distinguishes "genuinely not installed" from
    "installed but its native extension is broken", which look identical to a bare
    `except ImportError` but need completely different fixes.
    """
    import importlib
    try:
        importlib.import_module(module_name)
        return True, IMPORT_OK, f"{module_name} imports cleanly"
    except ImportError as e:
        missing_name = getattr(e, "name", None) or ""
        if missing_name == module_name or missing_name.startswith(module_name + "."):
            return False, IMPORT_NOT_INSTALLED, f"{module_name} is not installed"
        # The module itself resolved but a dependency failed to import. If the
        # missing piece looks like a compiled extension, call it out specifically.
        if ".so" in str(e) or "wrong ELF" in str(e) or "invalid ELF" in str(e):
            return False, IMPORT_BROKEN_NATIVE, (
                f"{module_name} is installed but a native extension failed to load: {e}"
            )
        return False, IMPORT_BROKEN, (
            f"{module_name} is installed but importing it failed: {e}"
        )
    except Exception as e:
        return False, IMPORT_BROKEN, f"{module_name} raised {type(e).__name__}: {e}"


def _installed_version(dist_name):
    """Return the currently installed version of a distribution, or None."""
    try:
        import importlib.metadata as md
        return md.version(dist_name)
    except Exception:
        return None


def repair_broken_native_extensions(max_passes=2):
    """Reinstall packages whose native extensions were built for another CPU.

    Reinstalls the **exact version already installed** rather than the latest one.
    That matters: a bare ``pip install --force-reinstall <pkg>`` resolves to the newest
    release and silently upgrades transitive pins, which breaks the environment in
    ways that look unrelated. (Reinstalling pydantic-core this way pulled 2.49.0 over
    a pydantic 2.13.5 that requires exactly 2.46.5, and litellm stopped importing.)

    ``--no-cache-dir`` matters too: pip's wheel cache happily serves the same
    wrong-arch wheel that caused the problem in the first place.

    Returns the list of distribution names that were successfully reinstalled.
    """
    repaired = []
    for _ in range(max_passes):
        broken = find_broken_native_extensions()
        packages = sorted({b["package"] for b in broken if b["package"]})
        if not packages:
            break
        # RECORD now describes the new binaries, so the cached map is stale.
        global _DIST_FILE_MAP
        _DIST_FILE_MAP = None
        print("  ⚠️  Native extensions built for a different CPU:")
        for item in broken[:10]:
            pkg = item["package"] or "<unknown>"
            print(f"       {pkg}: {item['reason']}")
            print(f"         {item['path']}")
        if len(broken) > 10:
            print(f"       ... and {len(broken) - 10} more")

        print("  🔧 Reinstalling affected packages at their current versions"
              " for this platform...")
        progress = []
        for pkg in packages:
            version = _installed_version(pkg)
            if version is None:
                # Not a real distribution name; reinstalling it would just error.
                print(f"  ⚠️  Skipping {pkg}: not an installed distribution")
                continue
            target = f"{pkg}=={version}"
            cmd = [get_python_executable(), "-m", "pip", "install",
                   "--force-reinstall", "--no-cache-dir", target]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                print(f"  ❌  Could not reinstall {target}: {e}")
                continue
            if res.returncode == 0:
                print(f"  ✅ Reinstalled {target}")
                progress.append(pkg)
            else:
                tail = (res.stderr or res.stdout or "").strip().splitlines()
                print(f"  ❌  Failed to reinstall {target}: "
                      f"{tail[-1] if tail else 'unknown error'}")
        for pkg in progress:
            if pkg not in repaired:
                repaired.append(pkg)
        if not progress:
            # Nothing could be reinstalled; another pass would repeat identically.
            break
    return repaired


# ─── Utility Functions ───────────────────────────────────────────────────────

def get_python_executable():
    """Return the python executable to use for pip installs.
    Prefers the workspace virtualenv if it exists."""
    venv_python = DEFAULT_VENV_PYTHON
    if os.path.isfile(venv_python) and os.access(venv_python, os.X_OK):
        return venv_python
    return sys.executable


def run_command(cmd, label="", timeout=300):
    """Run a command, return (rc, output). Labels help with verbose logging."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = p.stdout + p.stderr
        if label:
            print(f"  [{label}] rc={p.returncode}, output={output[:200] if output else 'empty'}...")
        return p.returncode, output
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def check_claude_cli_version():
    """Check if the claude CLI is on PATH and has a working native binary.
    Returns tuple of (is_ok, version_string)."""
    claude_path = shutil.which("claude")
    if claude_path is None:
        return False, None
    try:
        rc, out = run_command(["claude", "--version"])
    except OSError as e:
        # Exec format error or other OS-level error
        print(f"   ⚠️  claude binary exists but failed to execute: {e}")
        return False, None
    if rc != 0 or not out:
        return False, None
    has_valid_version = bool(re.match(r".*\d+\.\d+", out.strip()))
    no_error_messages = "error" not in out.lower() and "not installed" not in out.lower()
    return has_valid_version and no_error_messages, out.strip() if has_valid_version and no_error_messages else None


def print_failure_diagnostics(pkg_name, error):
    """Print detailed diagnostics and troubleshooting commands for installation failures."""
    print(f"\n  🔧 Troubleshooting commands for {pkg_name}:")
    print(f"     # Try installing manually with verbose output:")
    print(f"     python3 -m pip install -v {pkg_name}")
    print(f"     ")
    print(f"     # If permission issues, try with --user flag:")
    print(f"     python3 -m pip install --user {pkg_name}")
    print(f"     ")
    print(f"     # If pip is broken, try reinstalling pip:")
    print(f"     python3 -m ensurepip --upgrade")
    print(f"     python3 -m pip install --upgrade pip")
    print(f"     ")
    print(f"     # If network/proxy issues, try with alternative index:")
    print(f"     python3 -m pip install --index-url https://pypi.org/simple {pkg_name}")
    print(f"     ")
    print(f"     # If pip is broken, try reinstalling pip:")
    print(f"     python3 -m ensurepip --upgrade")
    print(f"     python3 -m pip install --upgrade pip")
    print(f"     ")
    print(f"     # Check Python and pip versions:")
    print(f"     python3 --version && python3 -m pip --version")
    print(f"     ")
    print(f"     # If all else fails, share the error above with another agent")
    print(f"     # for further troubleshooting.")


def install_package(pkg_name, pip_name=None, force=False):
    """Check if a package is importable; install via pip if not.

    When ``force`` is True, pip is invoked even if the package is already
    importable. This is used to repair installs that are missing optional
    extras (e.g. a bare ``litellm`` that lacks the ``[proxy]`` extras).

    A package that is installed but whose native extensions cannot load on this CPU
    is treated as *not* usable: the check below distinguishes that from a genuine
    absence, so a broken architecture-mismatched install is reported instead of being
    silently accepted as "already available".

    Returns True if package was available or successfully installed."""
    pip_name = pip_name or pkg_name
    if not force:
        ok, status, detail = diagnose_module(pkg_name)
        if ok:
            print(f"  ✅ {pkg_name} already available")
            return True
        if status in (IMPORT_BROKEN, IMPORT_BROKEN_NATIVE):
            # A plain reinstall of the same pin can be a no-op here; fix the native
            # layer first so the install below actually replaces the bad binaries.
            print(f"  ⚠️  {detail}")
            if repair_broken_native_extensions() and diagnose_module(pkg_name)[0]:
                print(f"  ✅ {pkg_name} available after native-extension repair")
                return True
    print(f"  📦 Installing {pip_name}...")

    # Try different installation strategies.
    # `verifiable` marks strategies that install into the environment this process
    # actually runs in, so a post-install import check is meaningful. `--user` and
    # `pipx` install elsewhere (and are typically absent from a venv's sys.path),
    # so for those pip's exit status is all we can trust.
    strategies = [
        # Strategy 1: Normal install
        ([get_python_executable(), "-m", "pip", "install", pip_name], "normal install", True),
        # Strategy 2: With --break-system-packages (for PEP 668 externally-managed environments)
        ([get_python_executable(), "-m", "pip", "install", "--break-system-packages", pip_name],
         "with --break-system-packages", True),
        # Strategy 3: With --user flag
        ([get_python_executable(), "-m", "pip", "install", "--user", pip_name], "with --user flag", False),
        # Strategy 4: Try pipx if available
        (["pipx", "install", pip_name], "with pipx", False),
    ]

    for cmd, description, verifiable in strategies:
        print(f"  📦 Installing {pip_name} ({description})...")
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
            # pip exiting 0 only means pip is happy. Verify the module actually
            # imports now, otherwise a reinstall that changed nothing would be
            # reported as a success and the caller would loop forever.
            if not verifiable:
                print(f"  ✅ Successfully installed {pip_name} ({description})")
                return True
            verified, status, detail = diagnose_module(pkg_name)
            if verified:
                print(f"  ✅ Successfully installed {pkg_name} ({description})")
                return True
            if status in (IMPORT_BROKEN, IMPORT_BROKEN_NATIVE):
                # Reinstalling the same spec cannot fix a broken native layer.
                print(f"  ⚠️  pip succeeded but {detail}")
                print("       Repairing native extensions for this architecture...")
                if repair_broken_native_extensions() and diagnose_module(pkg_name)[0]:
                    print(f"  ✅ {pkg_name} available after native-extension repair")
                    return True
                print(f"  ❌ {pkg_name} still unusable after native-extension repair")
                return False
            print(f"  ⚠️  pip reported success but {detail}; trying next strategy")
            continue
        except subprocess.CalledProcessError as e:
            print(f"  ⚠️  Failed ({description}): {e.stderr.strip() if e.stderr else 'Unknown error'}")
            continue
        except FileNotFoundError:
            # Command not found (e.g., pipx not installed)
            continue
        except Exception as e:
            print(f"  ⚠️  Unexpected error ({description}): {e}")
            continue

    # All strategies failed
    print(f"  ❌ Failed to install {pip_name} after trying all strategies")
    # Try to get the last error for diagnostics
    try:
        result = subprocess.run(
            [get_python_executable(), "-m", "pip", "install", pip_name],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        print_failure_diagnostics(pip_name, e)
    except Exception as e:
        print(f"  ❌ Unexpected error installing {pip_name}: {e}")
        print_failure_diagnostics(pkg_name, e)
    return False


# ─── Cache Management ──────────────────────────────────────────────────────

def read_cache(cache_file, default=None, as_int=False, as_set=False):
    """Read a value from a cache file."""
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                val = f.read().strip()
            if as_set:
                if val:
                    return set(val.split("\n"))
                return set()
            if as_int:
                if val.isdigit():
                    return int(val)
                return default
            return val if val else default
        except OSError:
            pass
    return default


def write_cache(cache_file, value):
    """Write a value to a cache file."""
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            if isinstance(value, set):
                f.write("\n".join(sorted(value)))
            else:
                f.write(str(value))
    except OSError:
        pass


# ─── API Key Management ─────────────────────────────────────────────────────

def get_and_cache_api_key(cache_file, env_var_name, clear=False):
    """Get an API key from environment or prompt user, then cache it."""
    # Handle clear flag
    if clear and os.path.exists(cache_file):
        try:
            os.remove(cache_file)
            print(f"🗑️  Cleared cached API key at {cache_file}.")
        except OSError:
            pass

    api_key = os.environ.get(env_var_name)
    if api_key:
        print(f"✅ {env_var_name} found in environment.")
        return api_key

    cached = read_cache(cache_file)
    if cached:
        print(f"✅ Found cached API key in {cache_file}.")
        os.environ[env_var_name] = cached
        return cached

    return None  # Caller should prompt


def cache_api_key(cache_file, api_key):
    """Cache an API key to a file."""
    if not api_key:
        return
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            f.write(api_key)
        print(f"💾 API key cached to {cache_file}.")
    except OSError as e:
        print(f"⚠️  Could not cache API key to {cache_file}: {e}")


# ─── Model Utilities ────────────────────────────────────────────────────────

def filter_chat_models(models, non_chat_keywords=None, free_keywords=None):
    """Filter models to only include chat models, separating free from standard."""
    if non_chat_keywords is None:
        non_chat_keywords = ["embed", "rerank", "guard", "clip", "siglip", "vector", "modality", "reward", "parse", "omni"]
    if free_keywords is None:
        free_keywords = ["community", "instruct", "chat", "free"]

    standard_chat_models = []
    free_tier_chat_models = []

    for model_obj in models:
        model_id = model_obj.get("id", "")
        owned_by = model_obj.get("owned_by", "").lower()
        model_id_lower = model_id.lower()

        if any(keyword in model_id_lower for keyword in non_chat_keywords):
            continue

        # Check if this is a free model
        is_free = (
            "community" in owned_by
            or any(keyword in model_id_lower for keyword in free_keywords)
            or "free" in model_id_lower
        )
        if is_free:
            if not any(m.get("id") == model_id for m in free_tier_chat_models):
                free_tier_chat_models.append(model_obj)
        else:
            if not any(m.get("id") == model_id for m in standard_chat_models):
                standard_chat_models.append(model_obj)

    standard_chat_models.sort(key=lambda m: m.get("id", ""))
    free_tier_chat_models.sort(key=lambda m: m.get("id", ""))

    return standard_chat_models, free_tier_chat_models, standard_chat_models + free_tier_chat_models


def format_token_count(tokens):
    """Format token count for display (e.g., 15000 -> 15k)."""
    if not tokens:
        return ""
    try:
        tokens = int(tokens)
    except (ValueError, TypeError):
        return str(tokens)
    if tokens >= 10000:
        return f"{tokens // 1000}k"
    elif tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)


def categorize_models(models, non_chat_keywords=None, free_keywords=None):
    """Wrapper to categorize models using filter_chat_models with provider-specific keywords.

    This is a convenience function that calls filter_chat_models with the provided keywords.
    Providers can pass their own non_chat_keywords and free_keywords for customization.
    """
    return filter_chat_models(models, non_chat_keywords, free_keywords)


# ─── NVIDIA Context Window Retrieval ──────────────────────────────────────────

def fetch_nvidia_context_windows(model_ids, cache_file, on_progress=None, timeout=20, force=False):
    """Resolve context windows for a list of NVIDIA NIM model ids.

    NVIDIA's ``integrate.api.nvidia.com/v1/models`` endpoint does NOT return
    context sizes (only id/object/created/owned_by). The authoritative source is
    NVIDIA's own catalog page at ``build.nvidia.com/<org>/<model>``, which embeds
    ``{"specifications": {"contextLength": <tokens>}}`` in its payload. This
    scrapes that value for each model and caches it to disk so repeat runs are
    instant and models only missing from the cache are (re)fetched.

    The cache is a JSON file mapping model id -> context token count. Only
    models NOT already present in the cache are fetched. Returns the merged
    dict of {model_id: context_window}.

    Args:
        model_ids: List of model IDs to resolve context for.
        cache_file: Path to the JSON cache file.
        on_progress: Optional callback(fetched, total, model_id) for progress.
        timeout: HTTP timeout in seconds.
        force: If True, return only cached values (skip live scrape entirely).
               Use this when the user explicitly opted out of scraping.
    """
    # Load any existing cache.
    cached = {}
    if cache_file and os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                cached = {k: int(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, json.JSONDecodeError):
            cached = {}

    # If force=True, return only what's cached (may be empty if no cache exists).
    # This is used when user opts out of scraping.
    if force:
        return dict(cached)

    missing = [mid for mid in model_ids if mid not in cached]

    results = dict(cached)
    if not missing:
        return results

    fetched = 0
    for mid in missing:
        url = f"https://build.nvidia.com/{mid}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                html = resp.read().decode("utf-8", "replace")
            m = re.search(r"contextLength\\?\"?\s*:\s*(\d+)", html)
            if m:
                results[mid] = int(m.group(1))
            fetched += 1
        except Exception:
            # Legacy/deprecated NIMs have stub catalog pages without contextLength.
            # Leave them absent so the caller falls back to its curated map.
            pass
        if on_progress:
            on_progress(fetched, len(missing), mid)
        elif fetched and fetched % 10 == 0:
            print(f"   📏 Scraped context for {fetched}/{len(missing)} uncached models...")

    # Persist the merged cache.
    if cache_file:
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            with open(cache_file, "w") as f:
                json.dump({k: v for k, v in results.items()}, f, indent=1)
        except OSError:
            pass

    return results


# ─── HTTP Utilities ───────────────────────────────────────────────────────

def http_get_json(url, api_key=None, use_query_param=False, timeout=30):
    """GET JSON from an API endpoint."""
    headers = {"Accept": "application/json", "User-Agent": "claude-bridge/1.0"}

    if api_key:
        if use_query_param:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}key={api_key}"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ─── Proxy Management ─────────────────────────────────────────────────────

def get_litellm_binary():
    """Find the litellm binary, trying multiple locations and install strategies."""
    import shutil

    # Look for the binary in a list of candidate locations
    candidate_paths = [
        "/workspace/.venv/bin/litellm",                    # workspace virtualenv
        os.path.expanduser("~/.local/bin/litellm"),        # pip --user
        os.path.expanduser("~/venv/bin/litellm"),          # user venv
        shutil.which("litellm"),                           # on PATH (system or pipx)
    ]
    for path in candidate_paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # Not found on disk — attempt to install litellm proxy extras
    print(f"   📦 Installing {LITELLM_PROXY_SPEC}...")

    # pip-driven strategies, ordered by least-to-most invasive
    install_strategies = [
        ([sys.executable, "-m", "pip", "install", "--user", "-q", LITELLM_PROXY_SPEC],
         "pip install --user"),
        ([sys.executable, "-m", "pip", "install", "-q", LITELLM_PROXY_SPEC],
         "pip install (default)"),
        (["pip", "install", "--user", "-q", LITELLM_PROXY_SPEC],
         "pip (PATH) --user"),
        ([sys.executable, "-m", "pip", "install", "-q", "--break-system-packages", LITELLM_PROXY_SPEC],
         "pip install --break-system-packages"),
    ]

    for cmd, desc in install_strategies:
        try:
            print(f"  📦 Trying {desc}...")
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            # Re-check all candidate locations after the install
            for path in candidate_paths:
                if path and os.path.isfile(path) and os.access(path, os.X_OK):
                    print(f"  ✅ litellm installed via {desc} at {path}")
                    return path
            # Also re-check PATH in case the shell picks it up fresh
            fresh = shutil.which("litellm")
            if fresh:
                return fresh
        except subprocess.CalledProcessError as e:
            err = (e.stderr or "").strip()
            print(f"  ⚠️  {desc} failed: {err[:200] if err else 'Unknown error'}")
        except FileNotFoundError as e:
            print(f"  ⚠️  {desc} failed (command not found): {e}")
        except Exception as e:
            print(f"  ⚠️  {desc} error: {e}")

    # Fallback: is litellm importable but simply missing a console entry point?
    try:
        import litellm
        version = getattr(litellm, "__version__", "unknown")
        print(f"  ℹ️  litellm is importable (v{version}) but no CLI binary was found on disk.")
        print("      The proxy needs the `litellm` console script; try "
              f"'pip install {LITELLM_PROXY_SPEC}'.")
    except ImportError:
        pass

    return None


def find_claude_pkg_dir(claude_prefix="@anthropic-ai/claude-code"):
    """Find the installed claude CLI package directory."""
    candidates = [
        "/usr/lib/node_modules",
        "/usr/local/lib/node_modules",
        os.path.expanduser("~/.nvm/versions/node"),
        os.path.expanduser("~/.npm-global/lib"),
    ]

    for base in candidates:
        if os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                if claude_prefix.split("/")[1] in dirs or f"{claude_prefix}" in files:
                    return os.path.join(root, claude_prefix)

    # Try npm root
    rc, out = run_command(["npm", "root", "-g"])
    if rc == 0:
        return os.path.join(out.strip(), claude_prefix)

    return None


def start_litellm_proxy(config_file, port, master_key, log_file, pid_file):
    """Start the litellm proxy in the background and wait until ready."""
    # Clean up any existing proxy
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        try:
            os.remove(pid_file)
        except OSError:
            pass

    os.makedirs(os.path.dirname(config_file), exist_ok=True)

    litellm_bin = get_litellm_binary()

    # If not found, try to install
    if litellm_bin is None:
        print(f"   📦 Installing {LITELLM_PROXY_SPEC} system-wide...")
        subprocess.run(
            ["pip", "install", "--break-system-packages", "-q", LITELLM_PROXY_SPEC],
            capture_output=True, timeout=180,
        )
        litellm_bin = get_litellm_binary()

    if litellm_bin is None:
        print("   ❌ Could not find or install litellm CLI binary")
        print(f"   Trying to install: {['pip', 'install', '-q', LITELLM_PROXY_SPEC]}")
        print(f"   Trying user install: {sys.executable} -m pip install --user -q {LITELLM_PROXY_SPEC}")
        if shutil.which("pipx"):
            print(f"   Trying pipx install: pipx install {LITELLM_PROXY_SPEC}")
        print(f"\n   💡 Troubleshooting command:")
        print(f"   {sys.executable} -m pip install --user {LITELLM_PROXY_SPEC}")
        print(f"   or: sudo {sys.executable} -m pip install {LITELLM_PROXY_SPEC}")
        return None

    with open(log_file, "w") as logf:
        proc = subprocess.Popen(
            [litellm_bin, "--config", config_file, "--port", str(port)],
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    with open(pid_file, "w") as f:
        f.write(str(proc.pid))

    print(f"🚀 Starting litellm proxy on port {port} (PID {proc.pid})...")

    # Wait for proxy to be ready with retries
    for i in range(60):  # Wait up to 60 seconds
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": f"Bearer {master_key}"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    print("   ✅ Proxy is ready.")
                    return proc
        except Exception:
            time.sleep(1)

    print("   ⚠️  Proxy did not become ready. Check the log below:")
    try:
        with open(log_file) as f:
            print(f.read()[-3000:])
    except OSError:
        pass
    return proc


def test_proxy_health(port, master_key, timeout=2):
    """Test if the proxy is healthy and responding."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/health",
            headers={"Authorization": f"Bearer {master_key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# ─── Claude CLI Management ────────────────────────────────────────────────

def get_claude_version():
    """Get the installed claude CLI version, or None if not installed."""
    if shutil.which("claude") is None:
        return None
    rc, out = run_command(["claude", "--version"])
    if rc != 0 or not out:
        return None
    if "error" in out.lower() or "not installed" in out.lower():
        return None
    return out.strip()


def install_claude_cli(args=None):
    """Install claude CLI via npm, handling devcontainer quirks."""
    print("   Current claude CLI version: not installed")

    # Check node availability
    print("   🔍 Checking node.js availability...")
    node_result = subprocess.run(["node", "-v"], capture_output=True, text=True, timeout=10)
    if node_result.returncode != 0:
        print("   ❌ Node.js not found. Cannot install claude CLI.")
        return False
    print(f"   ✅ Node.js available: {node_result.stdout.strip()}")

    if check_claude_cli_version()[0]:
        print("  ✅ claude CLI found (native binary OK).")
        return True

    print("   📦 Installing claude CLI via npm (@anthropic-ai/claude-code)...")

    npm_cmd = ["npm", "install", "-g", "--allow-scripts=@anthropic-ai/claude-code", "@anthropic-ai/claude-code", "--legacy-peer-deps", "--no-audit"]
    rc, out = run_command(npm_cmd, label="npm-install")
    print(f"   npm install rc={rc}")

    # Handle ENOTEMPTY/EPIPE
    if rc != 0 and ("ENOTEMPTY" in out or "EPIPE" in out or "npm error syscall rename" in out):
        print("   ENOTEMPTY/EPIPE detected - cleaning target directory...")
        run_command(["npm", "bin", "cache", "clean", "--force"])
        for cand in [
            "/home/vscode/.npm-global/lib/node_modules/@anthropic-ai/claude-code",
            "/usr/lib/node_modules/@anthropic-ai/claude-code",
            "/usr/local/lib/node_modules/@anthropic-ai/claude-code",
        ]:
            if os.path.isdir(cand):
                print(f"   Removing partial install at {cand}")
                shutil.rmtree(cand, ignore_errors=True)
        rc, out = run_command(npm_cmd, label="npm-install-retry")

    # Handle permission errors
    if rc != 0 and ("permission" in out.lower() or "EACCES" in out):
        print("   Install failed with permission error, retrying with sudo...")
        rc, out = run_command(["sudo"] + npm_cmd, label="npm-install-sudo")

    # Try to fix binary if needed
    pkg_dir = find_claude_pkg_dir()
    if pkg_dir:
        install_cjs = os.path.join(pkg_dir, "install.cjs")
        if os.path.exists(install_cjs):
            print("  🛠️  Fixing claude native binary...")
            for exe in ["bin/claude.exe", "bin/claude"]:
                stale = os.path.join(pkg_dir, exe)
                if os.path.isfile(stale):
                    try:
                        os.remove(stale)
                    except PermissionError:
                        run_command(["sudo", "rm", "-f", stale])
            run_command(["node", install_cjs], label="node-install")

    if check_claude_cli_version()[0]:
        print("  ✅ claude CLI installed and working.")
        return True

    print("  ⚠️  claude CLI installed but the native binary is not working.")
    return False


def _get_latest_npm_version(package_name):
    """Get the latest version of an npm package."""
    try:
        rc, out = run_command(["npm", "view", package_name, "version"])
        if rc == 0 and out.strip():
            return out.strip()
    except Exception:
        pass
    return ""


def _check_and_prompt_upgrade(package_name, display_name, current_version, args):
    """Check for available upgrade and prompt user if available.
    Returns True if upgrade was performed or not needed, False if failed."""
    # If --accept-all-defaults, skip upgrade check
    if args and args.accept_all_defaults:
        return True

    latest_version = _get_latest_npm_version(package_name)
    if not latest_version:
        print(f"   Latest {display_name} version: (unable to check)")
        return True

    # Extract just the version number (e.g., "2.1.261" from "2.1.261 (Claude Code)")
    current_version = current_version.split()[0] if current_version else current_version

    if latest_version and latest_version != current_version:
        print(f"   Latest {display_name} version: {latest_version}")
        try:
            resp = input(f"   Upgrade {display_name} from {current_version} to {latest_version}? (y/N): ").strip().lower()
        except EOFError:
            resp = "n"

        if resp == "y":
            print(f"   Upgrading {display_name} via npm...")
            npm_cmd = ["npm", "install", "-g", "--legacy-peer-deps", package_name]
            rc, out = run_command(npm_cmd, label=f"{package_name}-upgrade")

            # Handle ENOTEMPTY/EPIPE in devcontainers
            if rc != 0 and ("ENOTEMPTY" in out or "EPIPE" in out):
                print("   Upgrade encountered issues - attempting cleanup...")
                run_command(["npm", "bin", "cache", "clean", "--force"])
                rc, out = run_command(npm_cmd, label=f"{package_name}-upgrade-retry")

            # Handle permission errors
            if rc != 0 and ("permission" in out.lower() or "EACCES" in out):
                print("   Upgrade failed with permission error, retrying with sudo...")
                rc, out = run_command(["sudo"] + npm_cmd, label=f"{package_name}-upgrade-sudo")

            # Re-verify version after upgrade
            if "claude" in package_name:
                rc2, out2 = run_command(["claude", "--version"], label="version-after")
            else:
                # For other packages, try common command names
                bin_name = package_name.split("/")[-1].replace("-", "_")
                rc2, out2 = run_command([bin_name, "--version"], label="version-after")
            if rc2 == 0:
                new_version = out2.strip()
                print(f"   New {display_name} version: {new_version}")
            else:
                print(f"   ⚠️  Upgrade may have failed - version check returned non-zero")
        return True
    return True


def ensure_claude_cli(args=None):
    """Ensure claude CLI is available, installing if needed.

    This function:
    1. Checks if claude CLI is already installed and working
    2. If installed, checks for available upgrade and prompts (unless --accept-all-defaults)
    3. If not installed, auto-installs without prompting
    4. Returns True if claude CLI is available, False otherwise
    """
    # First check if claude is already installed and working
    is_ok, version = check_claude_cli_version()

    if is_ok:
        print(f"   Current claude CLI version: {version}")

        # Check for available upgrade and prompt if needed
        return _check_and_prompt_upgrade("@anthropic-ai/claude-code", "claude CLI", version, args)

    # Claude not installed - auto-install it (no prompt needed)
    return install_claude_cli(args)


def _litellm_proxy_deps_present():
    """Return True only when the litellm proxy has everything it needs at runtime.

    litellm's proxy has runtime deps that are NOT pulled in by a bare
    ``pip install litellm``:
      * the ``litellm`` console script (provided by the ``[proxy]`` extra)
      * the ``prisma`` module (provided by the ``extra-proxy`` extra), which the
        proxy's DB error-handler imports even when ``store_model_in_db`` is off.

    Missing either one surfaces as a confusing crash only after the proxy is up,
    so we check both explicitly up front.
    """
    if get_litellm_binary() is None:
        return False
    return diagnose_module("prisma")[0]


def ensure_litellm(args=None):
    """Ensure litellm (with proxy extras) is fully available, and check for upgrades."""
    print("   Checking litellm...")
    # Distinguish "litellm isn't installed" from "litellm is installed but one of its
    # compiled dependencies can't load on this CPU". The second case is invisible to a
    # plain `except ImportError` and reinstalling litellm itself never fixes it.
    importable, status, detail = diagnose_module("litellm")

    if not importable and status in (IMPORT_BROKEN, IMPORT_BROKEN_NATIVE):
        print(f"   ⚠️  {detail}")
        print("       This is usually a native extension built for a different CPU "
              "architecture than this machine.")
        if repair_broken_native_extensions():
            print("  🔄  Re-checking litellm after native-extension repair...")
            importable, status, detail = diagnose_module("litellm")

    if importable and _litellm_proxy_deps_present():
        print("  ✅ litellm already available")
        return _check_and_prompt_upgrade(LITELLM_PROXY_SPEC, "litellm", "installed", args)

    # Either the package is missing, or it's present but the proxy extras
    # (console script / prisma) are not. Install/repair with the proxy extras.
    if importable:
        print("   ⚠️  litellm is importable but proxy extras (prisma / CLI binary) are missing.")
        print(f"       Reinstalling with `{LITELLM_PROXY_SPEC}` to pull in the proxy runtime deps...")

    if install_package("litellm", LITELLM_PROXY_SPEC, force=True):
        # Re-verify the full runtime deps actually landed.
        if _litellm_proxy_deps_present():
            print(f"  ✅ {LITELLM_PROXY_SPEC} installed with all runtime dependencies")
            return _check_and_prompt_upgrade(LITELLM_PROXY_SPEC, "litellm", "installed", args)

        # Still broken. Report what is actually wrong instead of asserting
        # "prisma missing" when prisma is present but unimportable.
        missing = []
        if get_litellm_binary() is None:
            missing.append("CLI binary (litellm console script)")
        prisma_ok, prisma_status, prisma_detail = diagnose_module("prisma")
        if not prisma_ok:
            if prisma_status == IMPORT_NOT_INSTALLED:
                missing.append("prisma module")
            else:
                missing.append(f"prisma module ({prisma_detail})")

        if not prisma_ok and prisma_status != IMPORT_NOT_INSTALLED:
            # prisma is present but cannot be imported -> fix the native layer.
            print("   ⚠️  prisma is installed but will not import; repairing "
                  "native extensions rather than reinstalling litellm again...")
            if repair_broken_native_extensions() and _litellm_proxy_deps_present():
                print(f"  ✅ {LITELLM_PROXY_SPEC} available after native-extension repair")
                return _check_and_prompt_upgrade(
                    LITELLM_PROXY_SPEC, "litellm", "installed", args)

        print(f"   ❌ litellm installed but still unusable: {', '.join(missing)}")
        if not prisma_ok and prisma_status != IMPORT_NOT_INSTALLED:
            print("       The installed packages contain native extensions that do not "
                  "match this CPU architecture.")
            print(f"       Rebuild the environment for this platform, e.g.: "
                  f"{get_python_executable()} -m pip install --force-reinstall "
                  f"--no-cache-dir {LITELLM_PROXY_SPEC}")
        else:
            print("   💡 Try manually: "
                  f"{get_python_executable()} -m pip install --user {LITELLM_PROXY_SPEC}")
        return False
    return False


def ensure_prompt_toolkit(args=None):
    """Ensure prompt_toolkit is installed."""
    print("   Checking prompt_toolkit...")
    if install_package("prompt_toolkit", "prompt_toolkit"):
        return _check_and_prompt_upgrade("prompt_toolkit", "prompt_toolkit", "installed", args)
    return False


def ensure_native_extensions_healthy(args=None):
    """Repair compiled packages that were installed for the wrong CPU architecture.

    Every script in this family calls ``ensure_prerequisites``. A devcontainer image
    that mounts a virtualenv built on another architecture leaves every compiled
    dependency (pydantic_core, orjson, grpcio, numpy, ...) installed-but-unimportable.
    pip reports those requirements as satisfied, so the per-package checks below can
    never repair it on their own and every script fails with a misleading message.

    Run once up front, this fixes the whole environment in one pass so the individual
    ``ensure_*`` checks behave normally afterwards. Scans once per process.
    """
    global _NATIVE_SCAN_DONE
    print(f"🔍 Checking native extensions for this CPU "
          f"({platform.machine()})...")
    if _NATIVE_SCAN_DONE:
        return True
    _NATIVE_SCAN_DONE = True

    broken = find_broken_native_extensions()
    if not broken:
        print("  ✅ Native extensions match this architecture")
        return True

    packages = sorted({b["package"] for b in broken if b["package"]})
    print(f"  ⚠️  {len(broken)} native extension(s) cannot load on "
          f"{platform.machine()}; {len(packages)} package(s) affected")
    print("     (Typical cause: a virtualenv baked on another architecture was "
          "mounted into this container.)")

    _NATIVE_SCAN_DONE = False  # repair rescans; allow another pass afterwards
    repaired = repair_broken_native_extensions()
    _NATIVE_SCAN_DONE = True

    if repaired:
        print(f"  ✅ Repaired {len(repaired)} package(s): "
              f"{', '.join(repaired[:8])}"
              f"{' ...' if len(repaired) > 8 else ''}")
    still = find_broken_native_extensions()
    if still:
        print(f"  ⚠️  {len(still)} native extension(s) still cannot load; "
              "some features may be unavailable")
    else:
        print("  ✅ All native extensions now load correctly")
    return not still


def ensure_prerequisites(args=None):
    """Ensure litellm (with the proxy extras), prompt_toolkit, and the claude CLI are available."""
    print("🔍 Checking prerequisites...")
    ok = True
    ok &= ensure_native_extensions_healthy(args)
    ok &= ensure_litellm(args)
    ok &= ensure_prompt_toolkit(args)
    ok &= ensure_claude_cli(args)
    return ok


# ─── Cache Management (Extended) ────────────────────────────────────────────

def load_model_context(model_id):
    """Load the last context window for a specific model from cache."""
    cache_dir = os.path.expanduser("~/.claude_opencode_context_windows")
    cache_file = os.path.join(cache_dir, f"{model_id}.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                val = f.read().strip()
                return int(val) if val.isdigit() else None
        except (OSError, ValueError):
            pass
    return None


def save_model_context(model_id, context_window):
    """Save the context window for a specific model to cache."""
    try:
        cache_dir = os.path.expanduser("~/.claude_opencode_context_windows")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{model_id}.txt")
        with open(cache_file, "w") as f:
            f.write(str(context_window))
    except OSError:
        pass


def load_model_compaction(model_id):
    """Load the last auto-compaction threshold (%) for a specific model."""
    cache_dir = os.path.expanduser("~/.claude_opencode_context_windows")
    cache_file = os.path.join(cache_dir, f"{model_id}.compaction.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                val = f.read().strip()
                return int(val) if val.isdigit() else None
        except (OSError, ValueError):
            pass
    return None


def save_model_compaction(model_id, threshold):
    """Save the auto-compaction threshold (%) for a specific model."""
    try:
        cache_dir = os.path.expanduser("~/.claude_opencode_context_windows")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{model_id}.compaction.txt")
        with open(cache_file, "w") as f:
            f.write(str(threshold))
    except OSError:
        pass


def load_statusline_mode(cache_file=None):
    """Load the last used statusline mode ('full' or 'compact') from cache."""
    if cache_file is None:
        cache_file = os.path.expanduser("~/.claude_opencode_statusline_mode")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                val = f.read().strip().lower()
                if val in ("full", "compact"):
                    return val
        except OSError:
            pass
    return None


def save_statusline_mode(mode, cache_file=None):
    """Save the last used statusline mode ('full' or 'compact') to cache."""
    if cache_file is None:
        cache_file = os.path.expanduser("~/.claude_opencode_statusline_mode")
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            f.write(mode)
    except OSError:
        pass


# ─── Favorites Management ───────────────────────────────────────────────────

def load_favorites(cache_file=None):
    """Load the set of favorite model IDs from cache."""
    if cache_file is None:
        cache_file = os.path.expanduser("~/.claude_opencode_favorites")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                content = f.read().strip()
                if content:
                    return set(content.split("\n"))
        except OSError:
            pass
    return set()


def save_favorites(favorites_set, cache_file=None):
    """Save the set of favorite model IDs to cache."""
    if cache_file is None:
        cache_file = os.path.expanduser("~/.claude_opencode_favorites")
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            f.write("\n".join(sorted(favorites_set)))
    except OSError:
        pass


# ─── Terminal Utilities ─────────────────────────────────────────────────────

def get_terminal_height():
    """Return usable terminal height, clamped to a sane minimum."""
    try:
        import shutil
        rows = shutil.get_terminal_size().lines
        if rows and rows > 4:
            return rows
    except Exception:
        pass
    return 24


# ─── Proxy Utilities ────────────────────────────────────────────────────────

def port_open(port):
    """Check if a port is open."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop_running_proxy(pid_file=None, timeout=5):
    """Stop any previously-started proxy for this bridge."""
    if pid_file is None:
        pid_file = os.path.expanduser("~/.claude_opencode_proxy.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(timeout)
        except (OSError, ValueError):
            pass
        try:
            os.remove(pid_file)
        except OSError:
            pass


def test_proxy_connection(port, master_key, selected_model):
    """Quick validation via the proxy to verify the model responds."""
    print(f"\n🧪 Testing selected model through proxy ({selected_model})...")
    payload = {
        "model": selected_model,
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "Hello! Please respond with a simple greeting."}],
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {master_key}",
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            content = data.get("content", [])
            text = "".join(
                b.get("text", "")
                for b in content
                if b.get("type") == "text"
            )
            if not text.strip():
                text = "".join(
                    b.get("thinking", "")
                    for b in content
                    if b.get("type") == "thinking"
                )
            print(f"✅ Proxy test successful!")
            print(f"   Response: {text.strip()[:200]}")
            print(f"   Usage: {data.get('usage', {})}")
            return True
    except urllib.error.HTTPError as e:
        print(f"⚠️  Proxy test returned status {e.code}")
        print(f"   Error: {e.read().decode()[:500]}")
        return False
    except Exception as e:
        print(f"⚠️  Proxy test failed: {type(e).__name__}: {str(e)[:200]}")
        return False


# ─── Claude Code Persistence ─────────────────────────────────────────────

def setup_claude_persistence():
    """Set up Claude Code persistence using .claude_persist in workspace."""
    import shutil

    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.abspath(script_dir)
    claude_persist_dir = os.path.join(workspace_root, ".claude_persist")
    claude_config_dir = os.path.expanduser("~/.claude")

    os.makedirs(claude_persist_dir, exist_ok=True)

    if os.path.islink(claude_config_dir):
        try:
            current_target = os.readlink(claude_config_dir)
        except OSError:
            current_target = ""
        if current_target == claude_persist_dir:
            print(f"✅ ~/.claude already symlinked to {claude_persist_dir}")
            return

    elif os.path.isdir(claude_config_dir):
        if not os.listdir(claude_config_dir):
            shutil.rmtree(claude_config_dir)
            os.symlink(claude_persist_dir, claude_config_dir)
            print(f"  ✅ Symlinked empty ~/.claude → {claude_persist_dir}")
        else:
            # ~/.claude has content. .claude_persist may ALREADY be the real store
            # (that is the whole point of persistence), so never delete it: merge
            # into it instead. The previous rmtree here destroyed every persisted
            # session whenever a freshly created container happened to ship a
            # non-empty ~/.claude.
            if os.path.isdir(claude_persist_dir) and os.listdir(claude_persist_dir):
                print("  Merging ~/.claude into .claude_persist "
                      "(existing persisted data preserved)")
            else:
                print("  Copying existing ~/.claude to .claude_persist")
            shutil.copytree(claude_config_dir, claude_persist_dir, dirs_exist_ok=True)
            # Remove the original without following a symlink (rmtree refuses those).
            if os.path.islink(claude_config_dir):
                os.unlink(claude_config_dir)
            else:
                shutil.rmtree(claude_config_dir)
            os.symlink(claude_persist_dir, claude_config_dir)
            print(f"  ✅ Merged ~/.claude → {claude_persist_dir}")
    else:
        os.symlink(claude_persist_dir, claude_config_dir)
        print(f"  ✅ Created ~/.claude → {claude_persist_dir} (populated on first launch)")


# ─── Statusline Setup ─────────────────────────────────────────────────────

def setup_statusline_symlink(workspace_file="claude_statusline.sh", provider_indicator="opencode"):
    """Set up statusline file symlink in the Claude config directory."""
    claude_config_dir = os.path.expanduser("~/.claude")
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_statusline = os.path.join(workspace_dir, workspace_file)

    if not os.path.exists(workspace_statusline):
        print(f"⚠️  workspace statusline not found at {workspace_statusline}")
        return None

    statusline_dst = os.path.join(claude_config_dir, workspace_file)

    try:
        os.makedirs(os.path.dirname(statusline_dst), exist_ok=True)
        os.chmod(workspace_statusline, 0o755)
        if os.path.islink(statusline_dst) or os.path.exists(statusline_dst):
            os.unlink(statusline_dst)
        os.symlink(workspace_statusline, statusline_dst)
        print(f"📐 Symlinked workspace statusline → {statusline_dst}")

        settings_file = os.path.join(claude_config_dir, "settings.json")
        resolved_statusline = os.path.realpath(statusline_dst)
        # Don't hardcode CLAUDE_CODE_STATUSLINE_MODE - let the env var from launch_claude_with_model pass through
        statusline_command = f"bash {resolved_statusline}"

        if os.path.exists(settings_file):
            with open(settings_file, "r") as f:
                settings = json.load(f)
        else:
            settings = {}

        settings["statusLine"] = {
            "type": "command",
            "command": statusline_command
        }

        with open(settings_file, "w") as f:
            json.dump(settings, f, indent=2)

        print(f"⚙️  Configured statusLine in {settings_file}")
        return True
    except OSError as e:
        print(f"⚠️  Could not create symlink: {e}")
        return None
# End of claude_library.py