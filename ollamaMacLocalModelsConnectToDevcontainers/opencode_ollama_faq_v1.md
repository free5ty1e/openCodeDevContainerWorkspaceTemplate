# OpenCode + Ollama FAQ (v1)

## Q: Why don’t my newly pulled models show up in OpenCode?
A: OpenCode reads config at startup and merges config sources by precedence; it does not hot‑reload model lists. Restart OpenCode after editing config. citeturn23search219

## Q: Why use `-ctxNNNN` variants instead of setting `num_ctx` in OpenCode?
A: When using Ollama via the OpenAI‑compatible endpoint, per‑request control of `num_ctx` is not consistently supported, so a reliable approach is to bake it into a saved Ollama model tag. (Use `/set parameter num_ctx` then `/save`.)

## Q: I’m in a devcontainer — why does `localhost:11434` fail?
A: Inside a container, `localhost` is the container. Use `host.docker.internal` to reach host services with Docker Desktop. citeturn23search207

## Q: What’s the simplest “known good” default model?
A: Use a dedicated coding model as the default (e.g., `qwen2.5-coder:32b-ctx16384`) and keep a fast fallback like `llama3:8b-ctx32768` available.

## Q: How do I confirm the container can reach Ollama?
A: Run `curl http://host.docker.internal:11434/api/tags` inside the container. citeturn23search207

## Q: I have more RAM — how do I get larger contexts?
A: Run the adaptive installer on that machine; it tiers by total memory. You can also override tiers without editing the script using `CTX_*` env vars.
