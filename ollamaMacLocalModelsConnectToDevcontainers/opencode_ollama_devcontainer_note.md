# Devcontainer Note — Using Host Ollama from OpenCode in a Container

Docker Desktop provides `host.docker.internal` for containers to connect to services running on the host. citeturn23search207

## Recommended OpenCode provider baseURL

```jsonc
{
  "provider": {
    "ollama": {
      "options": {
        "baseURL": "http://host.docker.internal:11434/v1"
      }
    }
  }
}
```

## Test connectivity (inside the container)

```bash
curl http://host.docker.internal:11434/api/tags
```

If you get JSON, OpenCode can use the host’s Ollama models.
