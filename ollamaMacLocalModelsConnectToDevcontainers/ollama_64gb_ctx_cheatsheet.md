# Per-Model Context & Quantization Cheat Sheet (64GB)

All values assume **Q4_K_M** unless stated otherwise.

| ollama pull | Recommended --ctx | Max Safe --ctx | Notes |
|------------|-------------------|---------------|-------|
| llama3.3:70b | 16384 | 32768 | Reduce ctx before touching quant |
| qwen2.5-coder:32b | 16384 | 32768 | Best OpenCode default |
| qwen3-coder:30b | 16384 | 32768 | Agent loops stable |
| deepseek-r1:32b | 8192 | 16384 | KV cache heavy |
| phi4:14b | 16384 | 32768 | Handles long reasoning well |
| llama3:8b | 8192 | 65536 | Very tolerant to large ctx |
| llava:34b | 8192 | 16384 | Vision models scale poorly |
