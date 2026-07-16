# Project specific rules

- Always follow the rules in `.agent/rules.md`



# --- DANGER GUARDRAILS START ---
# ⚠️ DANGER MODE GUARDRAILS — Do Not Remove

You are running with **automatic permission approval**. Every tool call you
make is executed WITHOUT confirmation. This is a safety-critical mode.

## MANDATORY RESTRICTIONS — Git write operations

Only the following **Staging & Read** operations are allowed:

### ✅ ALLOWED Git Operations
| Command | Purpose |
|---------|---------|
| `git add <file>` | Stage a file (fine-grained) |
| `git add -p` | Stage interactively by hunk |
| `git add -A` | Stage all changes |
| `git status` | View working tree state |
| `git diff` | View unstaged changes |
| `git diff --cached` | View staged changes |
| `git log` | View commit history |
| `git show` | View a commit |
| `git blame` | Annotate a file |
| `git restore <file>` | Discard unstaged local changes |
| `git stash push` | Save WIP temporarily |
| `git stash list` | View stashes |
| `git stash show` | View stash contents |

### ❌ FORBIDDEN Git Operations
| Operation | Reason |
|-----------|--------|
| `git commit` | Would record changes permanently |
| `git push` / `git push --force` | Would publish to remote |
| `git branch` / `git checkout -b` | Would create branches |
| `git merge` / `git rebase` | Would alter history |
| `git tag` | Would tag releases |
| `git fetch` / `git pull` | Would contact remote |
| `git reset --hard` / `git reset --mixed` | Destructive history reset |
| `git revert` / `git cherry-pick` | Would create new commits |
| `git rm` / `git mv` | Would remove/rename tracked files |
| `git submodule` | Complex git mutation |
| `git worktree` | Would create worktrees |
| `git gc` / `git prune` / `git repack` | Repository maintenance |
| `git clean -fd` / `-fdX` | Aggressive file removal |
| `git stash drop` / `git stash pop` / `git stash clear` | Destructive stash ops |
| `git config` (with global/system) | Would change git settings |

### File System Cautions
- You can read, write, and edit files normally.
- **Do not delete files** without the user explicitly asking — even though
  you auto-accept permissions, ask for verbal confirmation on deletes.
- **Do not run shell commands** that modify the system (install packages,
  change system config) without asking first.

### Enforcement
- If you are asked to do a forbidden git operation, say:
  "⛔ This operation is blocked by Danger Mode guardrails."
- If in doubt, err on the side of refusing. The user can always switch to
  normal mode (`cz`) for git-write operations.

## MANDATORY RESTRICTIONS — az (Azure CLI)

Read-only operations are permitted. All write/mutation operations are prohibited.

### ❌ FORBIDDEN az Operations
| Operation | Reason |
|-----------|--------|
| `az resource create` / `az resource delete` / `az resource update` | Would create or delete Azure resources |
| `az vm start` / `az vm stop` / `az vm delete` | Would modify VM state |
| `az group create` / `az group delete` | Would modify resource groups |
| `az network *` (write subcommands) | Would modify network configuration |
| (any other az write operation) | Mutations are prohibited |

## MANDATORY RESTRICTIONS — gh (GitHub CLI)

Only read operations and updating PR descriptions via `gh edit` are permitted.

### ✅ ALLOWED gh Operations
| Command | Purpose |
|---------|---------|
| `gh edit` (PR description only) | Update PR descriptions |
| `gh pr view` / `gh issue view` / `gh repo view` | Read repository data |
| (any read-only gh command) | Read operations are permitted |

### ❌ FORBIDDEN gh Operations
| Operation | Reason |
|-----------|--------|
| `gh pr create` / `gh pr merge` / `gh pr close` | Would create or modify pull requests |
| `gh issue create` / `gh issue close` / `gh issue comment` | Would modify issues |
| `gh release create` | Would create releases |
| `gh repo fork` / `gh repo create` / `gh repo delete` | Would create or delete repositories |
| (any other gh write/mutation operation) | Mutations are prohibited |

# --- DANGER GUARDRAILS END ---
