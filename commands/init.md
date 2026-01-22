---
description: Initialize multi-review for your project
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

# Initialize Multi-Review

Set up multi-review for a project workspace. This command checks dependencies, discovers repositories, and creates a configuration file.

## Workflow

### 1. Explain Requirements

Display this message to the user:

```
Multi-Review Setup
==================

This tool orchestrates PR reviews across multiple AI tools (Claude Code, Codex, and Gemini).

IMPORTANT: Run this from your PROJECT ROOT directory - a folder that:
  - Is NOT itself a git repository
  - CONTAINS one or more git repositories as subdirectories

Example structure:
  ~/repos/my-project/           <- Run init HERE (not a git repo)
  ├── backend/                  <- git repo
  ├── frontend/                 <- git repo
  └── .multi-review.json        <- config file (created by init)

Reviews will be stored in: {project-root}/reviews/
```

### 2. Check Current Directory

First, verify the current directory is appropriate:

```bash
# Check if current directory IS a git repo (should NOT be)
git rev-parse --git-dir 2>/dev/null
```

If this succeeds (returns `.git` or a path), warn the user:

```
WARNING: Current directory appears to be a git repository.

Multi-review expects to run from a PROJECT ROOT that contains multiple repos,
not from inside a single repo.

Expected: ~/repos/my-project/  (contains backend/, frontend/, etc.)
You are in: {current_dir}
```

Ask the user if they want to continue anyway or abort.

### 3. Check Dependencies

Check each required dependency and build a status report:

```bash
# Check gh CLI
gh --version 2>/dev/null

# Check if gh is authenticated
gh auth status 2>/dev/null

# Check claude CLI
claude --version 2>/dev/null

# Check codex CLI
codex --version 2>/dev/null

# Check gemini CLI
gemini --version 2>/dev/null

# Check uv
uv --version 2>/dev/null

# Check jq
jq --version 2>/dev/null
```

Display results in a table:

```
Dependency Check
================

Required:
  ✓ gh CLI          (installed, authenticated)
  ✓ uv              (installed)
  ✓ jq              (installed)

Review Tools (at least one required):
  ✓ claude          (installed)
  ✓ codex           (installed)
  ✗ gemini          (not found - will be skipped)

Optional:
  ✓ Linear plugin   (enables ticket ID lookups like /multi-review:review PROJ-123)
```

If any REQUIRED dependency is missing, display installation instructions and abort:

```
Missing required dependencies:

gh CLI:
  brew install gh && gh auth login

uv:
  curl -LsSf https://astral.sh/uv/install.sh | sh

jq:
  brew install jq
```

If NO review tools are available, abort with an error.

### 4. Discover Git Repositories

Run this command to find all git repositories in the current directory:

```bash
for dir in */; do
  if [ -d "$dir/.git" ]; then
    echo "${dir%/}"
  fi
done
```

Based on the command output:

**If repositories ARE found**, display them and continue to step 5:
```
Found git repositories:
  - backend
  - frontend
```

**If NO repositories are found** (command output is empty), show this warning:
```
No git repositories found in current directory.

Make sure you're in a project root that contains your repos:
  ~/repos/my-project/
  ├── backend/     <- should be a git repo
  └── frontend/    <- should be a git repo
```
Then ask if they want to continue anyway (maybe they'll clone repos later).

### 5. Verify GitHub Remotes

For each discovered repo, verify it has a GitHub origin remote:

```bash
# For each repo directory
git -C {repo_dir} remote get-url origin 2>/dev/null
```

Parse the org from the URL. Common formats:
- `https://github.com/org/repo.git` → org/repo
- `git@github.com:org/repo.git` → org/repo
- `ssh://git@github.com/org/repo.git` → org/repo

Display what was detected:

```
Detected GitHub repositories:
  ✓ backend       -> acme-corp/python-api-server
  ✓ frontend      -> acme-corp/nextjs-frontend
  ✗ local-tools   -> (no GitHub remote)
```

Repos without GitHub remotes will be skipped (can't look up PRs for them).

If no repos have GitHub remotes, warn and ask if they want to continue anyway.

### 6. Ask About Repository Nicknames

For each discovered repo, ask if they want to set up a nickname:

```
Repository Nicknames (Optional)
===============================

Nicknames let you use short names in commands:
  /multi-review:review backend:123
instead of:
  /multi-review:review my-long-repo-name:123

Found repos:
  1. python-api-server
  2. nextjs-frontend
  3. shared-utilities

Would you like to set up nicknames for any repos?
```

Use AskUserQuestion with options:
- "Yes, let me configure nicknames"
- "No, I'll use full repo names"

If yes, for each repo ask what nickname they want (or skip):

```
Nickname for "python-api-server"?
  - backend
  - api
  - Skip (use full name)
  - Custom...
```

### 7. Ask About Ticket ID Pattern (Optional)

```
Ticket ID Detection (Optional)
==============================

Multi-review can extract ticket IDs from PR titles/branches to organize reviews.

Examples:
  - JIRA-123 -> reviews stored in reviews/JIRA-123/
  - GH-456   -> reviews stored in reviews/GH-456/

What prefix does your team use for ticket IDs?
```

Use AskUserQuestion with options:
- "JIRA" (JIRA-###)
- "GH" (GH-###)
- "Custom prefix..."
- "None (skip ticket detection)"

### 8. Ask About Default Review Tools

```
Review Tools
============

Which AI tools should run reviews by default?
(You can override this per-review with --tools flag)
```

Use AskUserQuestion with multi-select:
- Claude Code (recommended)
- Codex
- Gemini

Only show tools that passed the dependency check.

### 9. Create Configuration File

Write `.multi-review.json` to the current directory:

```json
{
  "version": "1",
  "reviewsDir": "./reviews",
  "repos": ["backend", "frontend", "shared-lib"],
  "repoAliases": {
    "backend": "python-api-server",
    "frontend": "nextjs-frontend"
  },
  "ticketPattern": "JIRA-\\d+",
  "defaultTools": ["claude", "codex", "gemini"]
}
```

Note:
- `repoAliases` maps nickname -> actual repo directory name
- GitHub org is detected automatically from each repo's origin remote (not stored in config)

### 10. Create Reviews Directory

```bash
mkdir -p ./reviews
```

### 11. Display Success

```
Multi-Review Initialized!
=========================

Configuration saved to: .multi-review.json
Reviews will be stored in: ./reviews/

Repositories: backend (python-api-server), frontend (nextjs-frontend), shared-lib
Ticket pattern: JIRA-###
Default tools: claude, codex, gemini

GitHub orgs are detected automatically from each repo's origin remote.

Usage:
  /multi-review:run backend:123          # Review a single PR
  /multi-review:run backend:123 frontend:456  # Review multiple PRs
  /multi-review:run JIRA-123             # Review PRs linked to a ticket

To reconfigure, run /multi-review:init again or edit .multi-review.json
```

---

## Error Handling

- If `.multi-review.json` already exists, ask if they want to reconfigure or abort
- If user cancels at any step, don't write partial config
- Validate GitHub org format (no spaces, valid characters)
- Validate ticket pattern is valid regex
