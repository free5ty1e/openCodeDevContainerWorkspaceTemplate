#!/bin/bash
# Wrapper script to set necessary environment variables and resource limits 
# before starting the Ollama server daemon.

# Set file descriptor limit for the service
ulimit -n 65536

# Export environment variable for Ollama to ensure it binds to the LAN IP
export OLLAMA_HOST=0.0.0.0:11434

# Execute the ollama serve command
/opt/homebrew/bin/ollama serve
