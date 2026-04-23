#!/bin/bash

# preflight.sh
# Parses PR input, validates PRs exist, and detects re-review scenarios
# Usage: preflight.sh "backend:123 frontend:456"
#
# Reads configuration from .multi-review.json in the current directory or parent directories.

set -e

INPUT="$1"

# Find config file by searching up from current directory
find_config() {
    local dir="$PWD"
    while [ "$dir" != "/" ]; do
        if [ -f "$dir/.multi-review.json" ]; then
            echo "$dir/.multi-review.json"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
}

CONFIG_FILE=$(find_config)
if [ -z "$CONFIG_FILE" ]; then
    echo '{"error": "No .multi-review.json found. Run /multi-review:init first."}'
    exit 1
fi

# Read config
CONFIG_DIR=$(dirname "$CONFIG_FILE")
REVIEWS_DIR_REL=$(jq -r '.reviewsDir // "./reviews"' "$CONFIG_FILE")
TICKET_PATTERN=$(jq -r '.ticketPattern // empty' "$CONFIG_FILE")

# Resolve reviews directory (relative to config file location)
if [[ "$REVIEWS_DIR_REL" == ./* ]]; then
    REVIEWS_DIR="$CONFIG_DIR/${REVIEWS_DIR_REL#./}"
else
    REVIEWS_DIR="$REVIEWS_DIR_REL"
fi

# Get GitHub org from a repo's origin remote
get_github_org() {
    local repo_dir="$1"
    local remote_url

    if [ ! -d "$CONFIG_DIR/$repo_dir/.git" ]; then
        echo ""
        return 1
    fi

    remote_url=$(git -C "$CONFIG_DIR/$repo_dir" remote get-url origin 2>/dev/null) || {
        echo ""
        return 1
    }

    # Parse org from various URL formats:
    # https://github.com/org/repo.git -> org
    # git@github.com:org/repo.git -> org
    # ssh://git@github.com/org/repo.git -> org
    if [[ "$remote_url" =~ github\.com[:/]([^/]+)/([^/]+)(\.git)?$ ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo ""
    fi
}

# Validate input
if [ -z "$INPUT" ]; then
    echo '{"error": "No PR input provided. Use format: repo:123 or nickname:456"}'
    exit 1
fi

# Resolve repo nickname to actual repo name using config
resolve_repo() {
    local input="$1"

    # Check if it's a nickname in repoAliases (nickname -> actual)
    local actual
    actual=$(jq -r --arg nick "$input" '.repoAliases[$nick] // empty' "$CONFIG_FILE")

    if [ -n "$actual" ]; then
        echo "$actual"
    else
        # Return as-is (might be the actual repo name)
        echo "$input"
    fi
}

# Parse a single PR token (repo:number or URL)
# Returns: repo:number:github_org (org is empty if not from URL)
parse_pr_token() {
    local token="$1"
    local repo=""
    local number=""
    local url_org=""

    # Check if it's a GitHub URL
    if [[ "$token" =~ github\.com/([^/]+)/([^/]+)/pull/([0-9]+) ]]; then
        # Extract from URL: github.com/org/repo/pull/123
        url_org="${BASH_REMATCH[1]}"
        repo="${BASH_REMATCH[2]}"
        number="${BASH_REMATCH[3]}"
    elif [[ "$token" =~ ^([^:]+):([0-9]+)$ ]]; then
        # Format: repo:number
        repo="${BASH_REMATCH[1]}"
        number="${BASH_REMATCH[2]}"
    else
        echo ""
        return
    fi

    # Resolve nickname to actual repo name
    repo=$(resolve_repo "$repo")

    # Return repo:number:org (org may be empty)
    echo "$repo:$number:$url_org"
}

# Main parsing logic
PRS_JSON="[]"
ERRORS=""
TICKET_ID=""

# Split input by spaces and process each token
for token in $INPUT; do
    parsed=$(parse_pr_token "$token")

    if [ -z "$parsed" ]; then
        ERRORS="${ERRORS}Invalid format: $token. Use repo:number (e.g., backend:123). "
        continue
    fi

    # Parse repo:number:url_org from the result
    repo="${parsed%%:*}"
    rest="${parsed#*:}"
    number="${rest%%:*}"
    url_org="${rest#*:}"

    # If org came from URL, use it; otherwise detect from repo's git remote
    if [ -n "$url_org" ]; then
        github_org="$url_org"
    else
        github_org=$(get_github_org "$repo")
        if [ -z "$github_org" ]; then
            ERRORS="${ERRORS}Could not detect GitHub org for $repo (check git remote). "
            continue
        fi
    fi

    # Validate PR exists using gh CLI
    pr_info=$(gh pr view "$number" --repo "$github_org/$repo" --json number,title,url,headRefName 2>/dev/null)

    if [ -z "$pr_info" ]; then
        ERRORS="${ERRORS}PR $number not found in $github_org/$repo. "
        continue
    fi

    # Extract info from PR response
    title=$(echo "$pr_info" | jq -r '.title')
    url=$(echo "$pr_info" | jq -r '.url')
    head_ref=$(echo "$pr_info" | jq -r '.headRefName')

    # Try to extract ticket ID from title or branch using configured pattern
    if [ -z "$TICKET_ID" ] && [ -n "$TICKET_PATTERN" ]; then
        # Use grep with the pattern
        if echo "$title" | grep -oE "$TICKET_PATTERN" > /dev/null 2>&1; then
            TICKET_ID=$(echo "$title" | grep -oE "$TICKET_PATTERN" | head -1)
        elif echo "$head_ref" | grep -oE "$TICKET_PATTERN" > /dev/null 2>&1; then
            TICKET_ID=$(echo "$head_ref" | grep -oE "$TICKET_PATTERN" | head -1)
        fi
    fi

    # Add to PRs array (include github_org per PR)
    pr_obj=$(jq -n \
        --arg repo "$repo" \
        --arg number "$number" \
        --arg title "$title" \
        --arg url "$url" \
        --arg head_ref "$head_ref" \
        --arg github_org "$github_org" \
        '{repo: $repo, number: ($number | tonumber), title: $title, url: $url, head_ref: $head_ref, github_org: $github_org}')

    PRS_JSON=$(echo "$PRS_JSON" | jq --argjson pr "$pr_obj" '. + [$pr]')
done

# Check for errors
if [ -n "$ERRORS" ]; then
    echo "{\"error\": \"$ERRORS\"}"
    exit 1
fi

# Check if we found any PRs
pr_count=$(echo "$PRS_JSON" | jq 'length')
if [ "$pr_count" -eq 0 ]; then
    echo '{"error": "No valid PRs found"}'
    exit 1
fi

# Generate identifier
if [ -n "$TICKET_ID" ]; then
    IDENTIFIER="$TICKET_ID"
else
    # Use first PR's repo-number as identifier
    first_repo=$(echo "$PRS_JSON" | jq -r '.[0].repo')
    first_number=$(echo "$PRS_JSON" | jq -r '.[0].number')
    IDENTIFIER="${first_repo}-${first_number}"
fi

# Set review directory and ensure it exists
REVIEW_DIR="$REVIEWS_DIR/$IDENTIFIER"
mkdir -p "$REVIEW_DIR"

# Check for existing reviews (re-review detection)
IS_REREVIEW=false
EXISTING_REVIEWS="[]"

# Check which review files exist (FEEDBACK_ prefixed)
for tool in claude codex gemini; do
    if [ -f "$REVIEW_DIR/FEEDBACK_${tool}.md" ]; then
        IS_REREVIEW=true
        EXISTING_REVIEWS=$(echo "$EXISTING_REVIEWS" | jq --arg tool "$tool" '. + [$tool]')
    fi
done

# Output JSON (github_org is now per-PR in the prs array)
jq -n \
    --argjson prs "$PRS_JSON" \
    --arg identifier "$IDENTIFIER" \
    --arg review_dir "$REVIEW_DIR" \
    --argjson is_rereview "$IS_REREVIEW" \
    --argjson existing_reviews "$EXISTING_REVIEWS" \
    --arg ticket_id "$TICKET_ID" \
    --arg config_dir "$CONFIG_DIR" \
    '{
        prs: $prs,
        identifier: $identifier,
        review_dir: $review_dir,
        is_rereview: $is_rereview,
        existing_reviews: $existing_reviews,
        ticket_id: (if $ticket_id == "" then null else $ticket_id end),
        project_root: $config_dir
    }'
