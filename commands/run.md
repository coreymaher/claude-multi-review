---
description: Run PR reviews across Claude Code, Codex, and Gemini
allowed-tools:
  - Bash
  - Write
  - Read
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
  - mcp__plugin_linear_linear__get_issue
---

# Multi-Tool PR Review

Orchestrate comprehensive PR reviews using Claude Code, Codex, and Gemini. Each tool writes its review to a markdown file, and all reviews are stored together for comparison.

## Workflow

### 1. Find Configuration

First, locate the `.multi-review.json` config file by searching up from the current directory:

```bash
# Find config
dir="$PWD"
while [ "$dir" != "/" ]; do
    if [ -f "$dir/.multi-review.json" ]; then
        echo "$dir/.multi-review.json"
        break
    fi
    dir=$(dirname "$dir")
done
```

If no config is found, inform the user:

```
No .multi-review.json found.

Run /multi-review:init to set up multi-review for this project.
```

Then stop.

### 2. Check for Ticket ID Input

First, check if the input matches the configured ticket pattern (from `.multi-review.json`).

Read the ticket pattern from config:
```bash
jq -r '.ticketPattern // empty' {config_file}
```

If a pattern is configured and the input matches it (e.g., `JIRA-123`, `PROJ-456`), attempt to look up associated PRs:

**For Linear tickets** (pattern like `PROJ-\d+`, `LIN-\d+`, etc.):

1. Use the Linear plugin to fetch the ticket:
   ```
   mcp__plugin_linear_linear__get_issue(id: "{ticket_id}")
   ```

2. Look for PR links in the ticket:
   - `attachments` array - look for GitHub PR URLs
   - `description` field - scan for GitHub PR URLs (pattern: `github.com/.*/pull/\d+`)
   - `branchName` field - if set, could search for open PRs with that branch

3. Extract PR information from found URLs:
   `https://github.com/{org}/{repo}/pull/{number}` → `{repo}:{number}`

4. If PRs are found, use those as input for the preflight script instead of the raw ticket ID.

5. If no PRs are found in the ticket, inform the user:
   ```
   No PRs found linked to {ticket_id}.

   Check that PRs are linked in the Linear ticket (attachments or description).
   ```
   Then stop.

**If input does NOT match the ticket pattern**, proceed directly to step 3 with the original input.

### 3. Parse and Validate Input

Run the preflight script to parse PR input and validate PRs exist:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh "$ARGUMENTS"
```

If a ticket ID was provided and PRs were found in step 2, pass those instead:
```bash
${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh "repo1:123 repo2:456"
```

**Expected input formats:**
- `JIRA-123` - Ticket ID (looks up linked PRs)
- `backend:123` - PR using repo nickname
- `my-repo:456` - PR using full repo name
- `backend:123 frontend:456` - Multiple PRs
- `https://github.com/org/repo/pull/123` - GitHub URL

Parse the JSON output to get:
- `prs`: Array of PR objects with `repo`, `number`, `title`, `url`, `head_ref`, `github_org`
- `identifier`: Review identifier (ticket ID if found, otherwise repo-number)
- `review_dir`: Path to review directory
- `is_rereview`: Boolean - true if review files already exist
- `existing_reviews`: Array of tool names with existing reviews
- `project_root`: Path to project root directory

If there's an error in the JSON output, display it and stop.

### 4. Read Config for Default Tools

Read the config file to get default tools:

```bash
jq -r '.defaultTools // ["claude", "codex", "gemini"] | join(",")' {config_file}
```

### 5. Gather PR Context

For each PR, gather the diff and metadata (use the `github_org` from each PR object):

```bash
gh pr view {number} --repo {github_org}/{repo} --json title,body,files,additions,deletions
gh pr diff {number} --repo {github_org}/{repo}
```

Store this context for use in reviews.

### 6. Write Metadata

Create or update `metadata.json` in the review directory:

```json
{
  "prs": [...],
  "identifier": "...",
  "started_at": "ISO timestamp",
  "is_rereview": true/false
}
```

### 7. Execute Reviews (in parallel)

Run the review tools in parallel using the Python script. Pass the PRs as JSON (from preflight output):

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/run-reviews.py \
  --review-dir {review_dir} \
  --project-root {project_root} \
  --prs-json '{prs_json}' \
  [--tools {tools}]
```

The `prs_json` should be the `prs` array from the preflight output, which includes `repo`, `number`, and `github_org` for each PR.

**Example:**
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/run-reviews.py \
  --review-dir ~/repos/myproject/reviews/JIRA-123 \
  --project-root ~/repos/myproject \
  --prs-json '[{"repo": "backend", "number": 351, "github_org": "acme-corp"}]'
```

The script writes reviews to:
- `{review_dir}/FEEDBACK_claude.md`
- `{review_dir}/FEEDBACK_codex.md`
- `{review_dir}/FEEDBACK_gemini.md`

**If a tool fails or times out**, you can retry just that tool:
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/run-reviews.py \
  --review-dir {review_dir} \
  --project-root {project_root} \
  --prs-json '{prs_json}' \
  --tools codex
```

### 8. Synthesize Reviews

After all tools have completed their reviews, read all feedback files:
- `{review_dir}/FEEDBACK_claude.md`
- `{review_dir}/FEEDBACK_codex.md`
- `{review_dir}/FEEDBACK_gemini.md`

Analyze the combined feedback and provide a synthesis that includes:

1. **Verdict**: Is the PR ready to merge or does it need changes?
   - "Ready to merge" - No significant issues found
   - "Needs minor updates" - Small issues that should be addressed
   - "Needs changes" - Significant issues that must be fixed before merging

2. **Issue Summary** (if updates needed):
   - Total number of issues flagged across all tools
   - Breakdown by severity (critical/high/medium/low)
   - Common themes (issues flagged by multiple tools)
   - Brief description of the most important issues

3. **Tool Agreement**: Note where tools agree/disagree on issues

Keep the summary concise - this is an executive overview, not a repeat of all the details.

### 9. Display Results

After all reviews and synthesis complete, display:

```
Review complete for {identifier}

Verdict: [Ready to merge / Needs minor updates / Needs changes]

[If needs updates, show brief issue summary]

Review files:
  - Claude Code: {review_dir}/FEEDBACK_claude.md
  - Codex: {review_dir}/FEEDBACK_codex.md
  - Gemini: {review_dir}/FEEDBACK_gemini.md

PR(s) reviewed:
  - {repo}#{number}: {title}
```

If this was a re-review, mention that previous feedback was considered.

---

## Usage

```bash
# Ticket ID (looks up linked PRs from Linear)
/multi-review:run JIRA-123
/multi-review:run PROJ-456

# Single PR with repo nickname
/multi-review:run backend:123

# Single PR with full repo name
/multi-review:run my-api-server:456

# Multiple related PRs
/multi-review:run backend:123 frontend:456

# PR URL
/multi-review:run https://github.com/acme-corp/backend/pull/123

# Re-review (automatic if previous review exists)
/multi-review:run backend:123
```

---

## Technical Notes

- Config file (`.multi-review.json`) is found by searching up from current directory
- Repo nicknames are defined in config under `repoAliases`
- Review identifier is derived from ticket ID (if pattern matches) or `{repo}-{number}`
- Re-review is automatic when review directory already contains feedback files
- Each tool writes its own output format - no enforced structure
