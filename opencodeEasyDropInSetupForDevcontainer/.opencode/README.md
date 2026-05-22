# OpenCode Workspace Configuration

This folder contains your OpenCode configuration and workspace-local storage.

## Setup

To set up OpenCode with workspace-local storage:

```bash
bash ./.opencode/setup.sh
```

This script will automatically:

- Install OpenCode (if not already installed)
- Create storage directories (`db/`, `cache/`)
- Configure OpenCode to use this folder for all persistent data
- Create an `.env` file for persistent configuration
- Detect your shell (bash, zsh, fish, etc.)
- Add the necessary configuration to your shell profile if not already present
- Make OpenCode configuration persistent across shell sessions and container rebuilds
- Validate the setup by checking:
  - OpenCode binary is available and functional
  - OPENCODE_HOME environment variable is correctly set
  - Storage directories were created
  - Configuration file (.env) is valid
  - opencode.json is syntactically correct (if jq is available)

## Running OpenCode

After setup, simply run:

```bash
opencode
```

OpenCode will use the configuration from `opencode.json` and store all session data locally in this folder.

## Files

- **opencode.json** — Your OpenCode configuration (providers, models, etc.)
- **.env** — Environment variables for workspace-local operation (auto-generated)
- **db/** — OpenCode database and session storage (auto-generated)
- **cache/** — OpenCode cache and temporary files (auto-generated)
- **.gitignore** — Ensures runtime data isn't committed to Git
- **setup.sh** — Setup script to initialize workspace-local storage

## Container Rebuilds

All data in this folder persists across devcontainer rebuilds. When you rebuild your container, your OpenCode configuration and session history will be preserved.
