# OpenCode DevContainer Project

A ready-to-use devcontainer template with [opencode](https://github.com/anomalyco/opencode) pre-installed, with built-in support for local AI models via [Ollama](https://ollama.com/). VS Code tasks let you launch opencode with any configured local model — fully offline, private, and ready to go.

## What's Inside

- **DevContainer** — Ubuntu 24.04 with Node.js 22, Python 3, and opencode CLI pre-installed with a workspace-mounted ai_working directory for persistent opencode sessions between rebuilds
- **Ollama Integration** — Local AI models (qwen2.5-coder, deepseek-coder-v2, qwen3, mistral-nemo) with one-click startup via VS Code tasks
- **VS Code Tasks** — Auto resume sessions on folder open, or launch fresh with your choice of local model
- **Persistent Data** — Sessions and model storage survive container rebuilds

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

> **Install ONE code editor** (all support Dev Containers):
(For ease of use, VS Code is recommended.  Especially in Windows; it will even automatically install docker WSL for you.)
> - [VS Code](https://code.visualstudio.com/) + [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) + [WSL extension (Windows)](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-wsl) (Windows: [install WSL first](https://aka.ms/wsl), VS Code will auto-enable Docker in WSL)
> - [Cursor](https://cursor.sh/)
> - [Google Antigravity](https://antigravity.google.com/download)
> - [Zed](https://zed.dev/) (via [zed-devcontainer](https://github.com/zed-industries/zed/tree/main/crates/zed_devcontainer) extension)
> - [IntelliJ IDEA](https://www.jetbrains.com/idea/) / [PyCharm](https://www.jetbrains.com/pycharm/) (via [Dev Containers plugin](https://plugins.jetbrains.com/plugin/21962-dev-containers))
> - [GNOME Builder](https://apps.gnome.org/Builder/) (built-in)

> **Install ONE container runtime**: (only if not using VS Code with WSL on Windows)
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
- Also auto-starts Ollama server if installed

### Task: Launch Fresh
- **Command Palette**: `Tasks: Run Task` → `Opencode: Launch Fresh`
- Starts a fresh opencode session in `/workspace`
- Also auto-starts Ollama server if installed

### Local Ollama Models

This project includes support for running local AI models via [Ollama](https://ollama.com/). This keeps your code local and private — no data leaves your machine.

#### Available Models

The following agent-capable models are configured (support tool calling):

| Model | Size | Purpose |
|-------|------|---------|
| qwen2.5-coder:7b | ~4.4GB | Fast code generation |
| deepseek-coder-v2:16b | ~9GB | GPT4-Turbo class coding |
| qwen3:8b | ~8GB | General purpose |
| mistral-nemo:12b | ~12GB | Reasoning and analysis |

**Total: ~33GB** (all models)

#### Adding/Changing Models

Models are defined in one place: [`.devcontainer/ollama_models.conf`](.devcontainer/ollama_models.conf)

To add or change models:
1. Edit `.devcontainer/ollama_models.conf` (format: `["model:name"]="description|size"`)
2. Update [`.vscode/tasks.json`](.vscode/tasks.json) tasks for new models
3. Update [`opencode.json`](opencode.json) with new model entries
4. Run `bash .devcontainer/check_models.sh` to verify sync

#### Checking Config Sync

Run the validation script to ensure all configs are consistent:
```bash
bash .devcontainer/check_models.sh
```

This checks that every model in `ollama_models.conf` exists in both `tasks.json` and `opencode.json`.

#### Ollama Tasks

- **`Ollama: Pull Models`** — Downloads selected models (~33GB total)
  - First prompt: Install all models? (A/n)
  - If 'n': individual prompts for each model with size/description
  - Can also set via env var: `OLLAMA_MODELS='model1 model2' bash .devcontainer/setup_ollama.sh`

- **`Ollama: Start Server`** — Starts Ollama server (idempotent — safe to run multiple times)

- **`Opencode + Ollama: <model>`** — Starts server + launches opencode with that model
  - Auto-selects any installed model as your coding assistant
  - Run from VS Code: `Tasks: Run Task` → select the model you want

#### Manual Ollama Usage
```bash
# Start Ollama server
bash .devcontainer/start_ollama.sh

# Pull specific model
ollama pull qwen2.5-coder:7b

# Run opencode with local model
opencode /workspace --model ollama/qwen2.5-coder:7b

# List installed models
ollama list
```

#### Notes
- Ollama downloads are cached — if a model fails mid-download, re-running will resume
- Models persist in Ollama storage, not in the container — survives rebuilds
- Port 11434 is forwarded for Ollama API access

## Project Structure

```
.devcontainer/
  ├── Dockerfile             # Container image definition
  ├── devcontainer.json     # Devcontainer config
  ├── ollama_models.conf    # Central model list (edit here to add/change models)
  ├── setup_devcontainer.sh # Post-create setup
  ├── setup_ollama.sh       # Model pulling script
  ├── start_ollama.sh      # Idempotent server startup
  ├── check_models.sh       # Verify configs are in sync
  └── launch_opencode.sh    # Launch/resume opencode
.vscode/
  └── tasks.json            # VS Code tasks for opencode + Ollama
opencode.json               # OpenCode Ollama provider config
.gitattributes              # Enforce LF line endings
```

## License

GNU General Public License v3 — See [LICENSE](./LICENSE) for details.