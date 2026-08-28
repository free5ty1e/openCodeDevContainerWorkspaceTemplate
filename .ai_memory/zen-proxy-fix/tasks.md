# Tasks — Zen Proxy API Error Fix

- [x] Reproduce the API error (proxy returned `500 UPSTREAM_API_KEY is not
      configured` for keyless free models).
- [x] Test upstream `https://opencode.ai/zen/v1` keyless: hy3-free/laguna OK,
      big-pickle 429 (free burst quota consumed by running opencode session),
      deepseek-v4-flash-free unavailable.
- [x] Fix `src/server.js`: remove empty-key hard block; keyless forward;
      pass upstream status + parsed error through.
- [x] Make model lists dynamic (free models change constantly):
      - Add `_cz_zen_models_endpoint`, `_cz_fetch_zen_models` (live `/models`
        fetch, 10-min cache `zen_models_cache.txt`, offline fallback),
        `_cz_fetch_free_models` (ids ending `-free` + `big-pickle`).
      - `cz-model` renders free AND paid sections from the live list.
      - `cz-test-free-models` uses the live free list, not a hardcoded array.
- [x] Fix zsh `local`-inside-loop echo leak in widget functions.
- [x] Fix stale model names from fresh installs:
      installed `.env.zen` had retired `hy3-preview-free` → 401; also
      `.env.example`, `.env.zen.example`, `src/config.js` default, and README
      flagged stale `deepseek-v4-flash-free` / `minimax-m2.5-free`.
- [x] Audit devcontainer dependencies (setup_claude_zen_devcontainer.sh flow,
      Dockerfile, devcontainer.json); only npm was stale.
- [x] Upgrade npm live 10.9.8 → 12.0.2; verify node/claude/prettier/opencode.
- [x] Persist Dockerfile changes: `apt-get upgrade` + `npm install -g npm@12.0.2`.
- [x] Reinstall wrapper block into ~/.bashrc and ~/.zshrc.
- [x] Verify: npm test 10/10, dynamic menu (65 entries, select+restart works),
      cz-test-free-models clean in bash+zsh, `cz` e2e, bash -n / zsh -n.

## Out of scope / follow-ups
- big-pickle keyless stays 429 while this opencode session consumes its free
  quota; retest with a fresh IP/quota window.
- `[claude-code:unrecognized_model]` is cosmetic; could add modelOverrides to
  fully silence per-session.