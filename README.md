# multi-review

A Claude Code plugin that orchestrates PR reviews across multiple AI tools (Claude Code, Codex, and Gemini) in parallel.

## Features

- **Multi-tool reviews**: Get perspectives from Claude, Codex, and Gemini simultaneously
- **Parallel execution**: All review tools run concurrently for faster results
- **Re-review support**: Automatically detects previous reviews and provides updated feedback
- **Flexible PR input**: Supports repo nicknames, full names, and GitHub URLs
- **Ticket ID lookup**: Pass a ticket ID (e.g., `PROJ-123`) to auto-discover linked PRs (requires Linear plugin)
- **Ticket ID detection**: Organizes reviews by ticket ID extracted from PR titles/branches
- **Per-repo GitHub org detection**: Automatically detects GitHub org from each repo's origin remote

## Installation

### Prerequisites

**Required:**
- [gh CLI](https://cli.github.com/) - GitHub command line tool (authenticated)
- [uv](https://github.com/astral-sh/uv) - Python package runner
- [jq](https://jqlang.github.io/jq/) - JSON processor

**Review tools (at least one):**
- [Claude Code](https://claude.ai/code) - `claude` CLI
- [Codex](https://openai.com/codex) - `codex` CLI
- [Gemini](https://ai.google.dev/) - `gemini` CLI

**Optional:**
- Linear plugin for Claude Code - enables ticket ID lookups (`/multi-review:run PROJ-123`)

### Install the plugin

```bash
# Add the marketplace and install
/plugin marketplace add coreymaher/claude-multi-review
/plugin install multi-review@claude-multi-review
```

For local development/testing:
```bash
git clone https://github.com/coreymaher/claude-multi-review.git
claude --plugin-dir /path/to/claude-multi-review
```

## Setup

### Directory Structure

Multi-review expects to run from a **project root** directory that:
- Is NOT itself a git repository
- Contains one or more git repositories as subdirectories

```
~/repos/my-project/           <- Project root (run commands here)
├── backend/                  <- Git repo (has origin remote)
├── frontend/                 <- Git repo (has origin remote)
├── .multi-review.json        <- Config file (created by init)
└── reviews/                  <- Review output directory
```

### Initialize

From your project root:

```
/multi-review:init
```

This will:
1. Check all dependencies are installed
2. Discover git repositories in the current directory
3. Verify each repo has a GitHub origin remote
4. Let you set up repo nicknames (optional)
5. Configure ticket ID pattern (optional)
6. Create `.multi-review.json` config file

## Usage

```bash
# Review by ticket ID (looks up linked PRs from Linear)
/multi-review:run JIRA-123
/multi-review:run PROJ-456

# Review a single PR
/multi-review:run backend:123

# Review multiple related PRs
/multi-review:run backend:123 frontend:456

# Use full repo name
/multi-review:run my-api-server:123

# Use GitHub URL
/multi-review:run https://github.com/acme/backend/pull/123
```

## Configuration

The `.multi-review.json` file:

```json
{
  "version": "1",
  "reviewsDir": "./reviews",
  "repos": ["backend", "frontend"],
  "repoAliases": {
    "api": "python-api-server",
    "web": "nextjs-frontend"
  },
  "ticketPattern": "JIRA-\\d+",
  "defaultTools": ["claude", "codex", "gemini"]
}
```

| Field | Description |
|-------|-------------|
| `reviewsDir` | Where to store review output (relative to project root) |
| `repos` | List of repo directories discovered during init |
| `repoAliases` | Short names for repos (nickname → actual directory) |
| `ticketPattern` | Regex to extract ticket IDs from PR titles/branches |
| `defaultTools` | Which review tools to run by default |

## Output

Reviews are stored in `{reviewsDir}/{identifier}/`:

```
reviews/
└── JIRA-123/
    ├── FEEDBACK_claude.md
    ├── FEEDBACK_codex.md
    ├── FEEDBACK_gemini.md
    └── metadata.json
```

The identifier is:
- Ticket ID if detected from PR title/branch (e.g., `JIRA-123`)
- Otherwise `{repo}-{number}` (e.g., `backend-456`)

## Re-reviews

Running a review for a PR that was previously reviewed automatically triggers a re-review. Each tool reads its previous feedback and notes what's been addressed vs. new issues.

## License

MIT
