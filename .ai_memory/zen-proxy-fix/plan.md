# Plan — Zen Proxy API Error Fix

## Diagnosis
1. Local proxy refused keyless free-model requests (`500 UPSTREAM_API_KEY is
   not configured`) → Claude CLI showed "API error".
2. Behind that: some free models are transiently rate-limited from this IP
   (big-pickle is actively used by the running opencode session).

## Fixes
- Allow keyless forwarding in `server.js` (send auth only when key present)
  and surface the real upstream error/status.
- Add `cz-test-free-models` to periodically test which free models respond.
- Keep model picker and defaults in sync with the live upstream model list.
- Make the backgrounded proxy survive its launching shell.

## Residual risk
- Free-model availability is volatile; `big-pickle` keyless may still 429
  while this opencode session runs. `cz-test-free-models` documents the live
  state, and the picker lists many working alternatives.