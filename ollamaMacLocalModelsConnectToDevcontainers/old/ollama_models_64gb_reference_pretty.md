# Ollama Models for 64 GB Unified Memory Systems (Readable Edition)

This version reformats the tables for **human readability**. All models listed are practical on a **64 GB unified RAM/VRAM system** (e.g., Apple Silicon) using typical `Q4_K_M` quantization with reasonable context sizes.

---

## General / Instruction Models

| Model Family        | Supported Variants (≤64 GB)        | Max Params | Fits Comfortably? | Typical Notes |
|---------------------|------------------------------------|------------|-------------------|---------------|
| **Llama 3 / 3.1 / 3.3** | 8B, 70B                            | 70B        | ✅ Yes            | Best all‑around model family; 70B is a flagship daily driver |
| **Llama 3.2**       | 1B, 3B                             | 3B         | ✅ Yes            | Extremely fast, low‑resource utility models |
| **Mistral**         | 7B                                 | 7B         | ✅ Yes            | Fast, stable, strong instruction adherence |
| **Gemma 2 / 3**     | 2B, 9B, 12B, 27B                   | 27B        | ✅ Yes            | Very efficient; surprisingly strong at mid‑sizes |
| **Phi‑3 / Phi‑4**   | 3.8B, 14B                          | 14B        | ✅ Yes            | Excellent reasoning per parameter |
| **Vicuna**          | 7B, 13B                            | 13B        | ✅ Yes            | Natural conversational fine‑tunes |
| **Neural Chat**     | 7B                                 | 7B         | ✅ Yes            | Clear, structured answers |
| **Starling**        | 7B                                 | 7B         | ✅ Yes            | Precise instruction‑following |
| **Orca Mini**       | 3B, 7B, 13B                        | 13B        | ✅ Yes            | Designed for efficiency on smaller hardware |

---

## Coding / Developer‑Focused Models

| Model Family            | Supported Variants (≤64 GB)                  | Max Params | Fits Comfortably? | Typical Notes |
|-------------------------|----------------------------------------------|------------|-------------------|---------------|
| **Qwen2.5‑Coder**       | 0.5B, 1.5B, 3B, 7B, 14B, 32B                 | 32B        | ✅ Yes            | Best overall local coding performance |
| **Qwen3‑Coder**         | 4B, 8B, 14B, 30B                              | 30B        | ✅ Yes            | Designed for agentic and tool‑based coding |
| **Code Llama**          | 7B, 13B, 34B                                  | 34B        | ✅ Yes            | Multi‑language coding, mature ecosystem |
| **StarCoder**           | 7B, 15B                                       | 15B        | ✅ Yes            | Code completion and generation |
| **WizardLM‑2**          | 7B                                           | 7B         | ✅ Yes            | Good balance of reasoning + coding |

---

## Reasoning / Math‑Heavy Models

| Model Family        | Supported Variants (≤64 GB)        | Max Params | Fits Comfortably? | Typical Notes |
|---------------------|------------------------------------|------------|-------------------|---------------|
| **DeepSeek‑R1**     | 7B, 8B, 14B, 32B                   | 32B        | ✅ Yes            | Strong chain‑of‑thought reasoning |
| **Qwen3 (General)** | 4B, 8B, 14B, 30B, 32B              | 32B        | ✅ Yes            | Reasoning + tools + long context |
| **Phi‑4**           | 14B                                | 14B        | ✅ Yes            | Very strong math/STEM accuracy |

---

## Multimodal (Vision + Tools)

| Model Family            | Supported Variants (≤64 GB)        | Max Params | Fits Comfortably? | Typical Notes |
|-------------------------|------------------------------------|------------|-------------------|---------------|
| **LLaVA**               | 7B, 13B, 34B                       | 34B        | ✅ Yes            | Image + text understanding |
| **Gemma (Vision)**     | 12B, 27B                           | 27B        | ✅ Yes            | Efficient multimodal assistant |
| **Qwen3.5 Vision**     | 4B, 9B, 27B, 35B                   | 35B        | ✅ Yes            | Vision, tools, structured output |

---

## Embedding Models (RAG)

| Model Family            | Approx. Size | Fits Comfortably? | Typical Notes |
|-------------------------|--------------|-------------------|---------------|
| **nomic‑embed‑text**    | ~300 MB       | ✅ Yes            | Common RAG default |
| **mxbai‑embed‑large**   | ~335 MB       | ✅ Yes            | Higher‑quality embeddings |

---

## Excluded (Not Practical on 64 GB)

| Model Family        | Reason |
|---------------------|--------|
| **Llama 3.1 405B**  | Exceeds 140 GB even quantized |
| **Qwen3 235B**      | Well beyond 64 GB |
| **Qwen3.5 122B**    | Requires ≥96 GB |
| **GPT‑OSS 120B**    | Unstable within memory constraints |
| **Mixtral 8×22B**   | ~80 GB minimum |

---

## Practical Picks

- **Best daily driver:** Llama 3.3 70B
- **Best coding:** Qwen2.5‑Coder 32B
- **Best agent workflows:** Qwen3‑Coder 30B
- **Best RAG generator:** Llama 3 70B + nomic‑embed‑text

---

*Formatted for readability. Verified against Ollama library and memory sizing guidance as of May 2026.*
