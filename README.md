# OpenCode DevContainer Project

A ready-to-use devcontainer template with [opencode](https://github.com/anomalyco/opencode) pre-installed, plus VS Code tasks to automatically launch or resume your last session.

## What's Inside

- **DevContainer** — Ubuntu 24.04 with Node.js 22, Python 3, and opencode CLI pre-installed
- **VS Code Tasks** — Launch opencode fresh or resume the most recent session automatically on folder open
- **Setup Scripts** — Initialized on container creation

## Quick Start

### Prerequisites

> **Pick ONE editor** (all support Dev Containers):
> - [VS Code](https://code.visualstudio.com/) + [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
> - [Cursor](https://cursor.sh/)
> - [Google Antigravity](https://antigravity.google.com/download)
> - [Zed](https://zed.dev/) (via [zed-devcontainer](https://github.com/zed-industries/zed/tree/main/crates/zed_devcontainer) extension)
> - [IntelliJ IDEA](https://www.jetbrains.com/idea/) / [PyCharm](https://www.jetbrains.com/pycharm/) (via [Dev Containers plugin](https://plugins.jetbrains.com/plugin/21962-dev-containers))
> - [GNOME Builder](https://apps.gnome.org/Builder/) (built-in)

> **Pick ONE container runtime**:
> - [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac)
> - [Docker CLI](https://docs.docker.com/engine/install/) + [Docker Compose plugin](https://docs.docker.com/compose/install/) (Linux)
> - [Rancher Desktop](https://rancherdesktop.io/) (Windows/Mac/Linux alternative)
> - [Colima](https://github.com/abiosoft/colima) (macOS/Linux alternative)
> - [WSL2 + Docker Engine](https://docs.docker.com/engine/install/) (Windows 10 LTSC — run `wsl --install`, then install Docker inside Linux)

### Steps

1. Open this folder in your editor
2. When prompted, click **Reopen in Container** (or run `Dev Containers: Reopen in Container` from the command palette)
3. The devcontainer will build and automatically launch opencode

### Persistent Data

The following folders are mounted from your host into the container and persist across rebuilds:

- **`.ai_working/opencode_data`** — opencode session data, logs, and state
- **`.ai_memory`** — (if present) AI memory/context files

This means your opencode sessions, conversation history, and any cached data survive container rebuilds.

## Usage

### Task: Resume Last Session
- **Command Palette**: `Tasks: Run Task` → `Opencode: Resume Last Session`
- Automatically finds the most recent session and resumes it
- Runs automatically when you open the folder in VS Code

### Task: Launch Fresh
- **Command Palette**: `Tasks: Run Task` → `Opencode: Launch Fresh`
- Starts a fresh opencode session in `/workspace`

### Manual Usage
```bash
# Start a new session
opencode /workspace

# Resume a specific session
opencode /workspace -c -s <session-id>

# List all sessions
opencode session list
```

## Project Structure

```
.devcontainer/
  ├── Dockerfile          # Container image definition
  ├── devcontainer.json  # Devcontainer config
  ├── setup_devcontainer.sh
  ├── launch_opencode.sh
  └── ...
.vscode/
  └── tasks.json         # VS Code tasks for opencode
```

## License

GNU General Public License v3 — See [LICENSE](./LICENSE) for details.