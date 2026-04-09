# OpenCode DevContainer Project

A ready-to-use devcontainer template with [opencode](https://github.com/anomalyco/opencode) pre-installed, plus VS Code tasks to automatically launch or resume your last session.

## What's Inside

- **DevContainer** — Ubuntu 24.04 with Node.js 22, Python 3, and opencode CLI pre-installed with a workspace-mounted ai_working directory for persistent opencode sessions between rebuilds
- **VS Code Tasks** — Will auto resume the most recent session automatically on folder open, with another task to start a fresh session

## Get Started

### Option 1: Fork on GitHub (recommended)
1. Go to the repository page on GitHub
2. Click the **Fork** button in the top-right corner
3. Select your GitHub account as the destination
4. Clone your forked repository locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/your-repo-name.git
   cd your-repo-name
   ```

### Option 2: Download as ZIP
1. Go to the repository page on GitHub
2. Click the green **Code** button
3. Select **Download ZIP**
4. Extract the ZIP to your desired location

### Next Steps
Once you have a local copy, move on to the [Quick Start](#quick-start) section below to launch the devcontainer and begin developing with an opencode agent.

Fork it, rename it, strip it down or build on top — this is your base project. Modify it to suit your needs and start building.

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
4. If you are using Google Antigravity, it might take two launches to fully load the opencode session when building / rebuilding the devcontainer
  a. The first launch's output will create the container and end with "Container started" and just wait there.
  b. The second (and subsequent) launch(es) will pick up where that left off and actually end with opencode running.

### Persistent Data

The following folders are mounted from your host into the container and persist across rebuilds:

- **`.ai_working/opencode_data`** — opencode session data, logs, and state
- **`.ai_memory`** — AI memory/context files should be saved here if you point out the `.agent/rules.md` file to opencode.

This means your opencode sessions, conversation history, and any cached data survive container rebuilds.

## Agent Rules

This project includes a **`/workspace/.agent/rules.md`** file that defines how the AI agent should behave — memory handling, git behavior, and more.

**At the start of every conversation, point opencode to this file:**

```
Here's the rules file: /workspace/.agent/rules.md
```

This ensures the agent follows your conventions for session management, memory organization, and git behavior throughout your project.

An easy way to point to a file is to drag it from the left sidebar to the chat window.  A reference to that file will be inserted where your chat cursor is, so you can insert the file into your message in the appropriate place to make sense.


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