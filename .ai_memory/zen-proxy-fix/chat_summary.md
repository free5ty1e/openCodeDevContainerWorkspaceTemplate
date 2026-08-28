# Chat Summary — Zen Proxy / Claude Code API Error Fix

Session: 2026-08-28. Goal: make `cz-model` + `cz-danger` (Claude Code via
claude-code-zen-proxy → OpenCode Zen) work instead of "API error".

## Root cause found
The local proxy (`/workspace/.claude_zen/repo/src/server.js`) hard-returned
`500 UPSTREAM_API_KEY is not configured` whenever `UPSTREAM_API_KEY` was empty,
BEFORE forwarding the request. Since the setup script supports keyless free
models, every free-model request died at the proxy with "API error".

## Secondary finding (transient)
Free-tier keyless requests for `big-pickle` return
`429 FreeUsageLimitError / Rate limit exceeded` from this container's IP
because the running opencode session itself consumes big-pickle's free burst
quota. `hy3-free`, `nemotron-3-ultra-free`, `ling-3.0-flash-fin-free` respond.
Availability fluctuates minute to minute; `cz-test-free-models` shows the live
picture.

## Fixes applied
1. `src/server.js` (proxy):
   - Removed the empty-`UPSTREAM_API_KEY` hard block; forward keyless.
   - Only send `Authorization` header when a key is set.
   - Pass through upstream status code + parsed error type/message instead of a
     generic error.
2. `setup_claude_zen_devcontainer.sh` wrapper block:
   - New dynamic model discovery: `_cz_zen_models_endpoint`,
     `_cz_fetch_zen_models` (live `/models` fetch, 10-min cache at
     `__PERSISTENCE_DIR__/zen_models_cache.txt`, offline fallback snapshot),
     `_cz_fetch_free_models`.
   - `cz-model` menu is now built live from the upstream model list — free
     section = ids ending in `-free` (plus `big-pickle`), paid section = the
     rest. No hardcoded free list. Custom-model option is the last entry.
   - `cz-test-free-models` probes the live free list instead of a hardcoded
     array.
   - Fixed zsh quirk: `local` declared INSIDE a `for`/`while` loop echoes the
     assignments in zsh; all locals are declared at function top.
3. Model staleness fixes (the user hit
   `401 (hy3-preview-free): Model hy3-preview-free is not supported` — the
   installed `.env.zen` still carried the retired name):
   - `/workspace/.claude_zen/.env.zen`: `UPSTREAM_MODEL` → `hy3-free`.
   - `.env.example` / `.env.zen.example` (proxy repo): `deepseek-v4-flash-free`
     → `hy3-free`.
   - `src/config.js`: default `upstreamModel` fallback → `hy3-free`.
   - `README.md` + `PROXY_RESOURCES_AND_MODEL_SWITCHING.md`: replaced the stale
     `deepseek-v4-flash-free` / `minimax-m2.5-free` references with `hy3-free`.
4. Devcontainer dependency audit (all current except npm):
   - Node v22.23.2, opencode v1.18.25, claude 2.1.251, prettier 3.9.6, ollama
     0.33.1, git 2.43.0, curl 8.5.0, python 3.12.3 are the latest available.
   - npm was stale: 10.9.8 → upgraded live to **12.0.2** (proxy repo has zero
     external deps, so nothing else to bump).
   - Persisted in `/workspace/.devcontainer/Dockerfile`: added `apt-get
     upgrade` to the package RUN and pinned `npm install -g npm@12.0.2` after
     the Node install.
5. Earlier in this session: proxy daemon detached (`setsid nohup ... </dev/null
   &` + `disown`), stale-pid handling in `cz-model` restart,
   `CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1` in the settings
   file, model picker rewritten for bash+zsh.

## Verification (all passing)
- Proxy `npm test`: 10/10.
- `_cz_fetch_free_models` identical output in bash and zsh (9 live free models,
  no leaked assignment lines).
- `cz-model` dynamic menu: 65 entries rendered from live API; selecting 5
  (hy3-free) wrote `.env.zen`, restarted the proxy (PID 40300), health OK.
- `cz-test-free-models` (zsh): clean table; Working hy3-free,
  nemotron-3-ultra-free, ling-3.0-flash-fin-free; 429 big-pickle/mimo-v2.5-free.
- `cz -p "..."` end-to-end through proxy: "final sweep ok".
- `bash -n` / `zsh -n` pass on the installed wrapper block and the setup script.

## Notes / gotchas
- The `[claude-code:unrecognized_model]` notice is cosmetic; requests succeed.
- Free-model availability is volatile. Re-run `cz-test-free-models` before
  relying on a specific free model; `cz-model` always mirrors the live list.
- `.bashrc` has a non-interactive early-return guard, so `bash -c 'source
  ~/.bashrc'` will not load the aliases; in a real terminal they work.
- The proxy repo is a fork/mainline clone at HEAD 40ab568; our keyless free
  flow is an extension beyond the README (which assumes an API key).