# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""
Run PR reviews in parallel using Claude Code, Codex, and Gemini.

Usage:
    # Run all tools
    uv run run-reviews.py --review-dir /path/to/reviews/ID --github-org myorg --repo myrepo --pr 351

    # Multiple PRs
    uv run run-reviews.py --review-dir /path/to/reviews/ID --github-org myorg \
        --repo repo1 --pr 351 --repo repo2 --pr 123

    # Run specific tools (e.g., retry a failed tool)
    uv run run-reviews.py --review-dir /path/to/reviews/ID --github-org myorg \
        --repo myrepo --pr 351 --tools codex
"""

import argparse
import asyncio
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path


async def _communicate_with_cleanup(
    proc: asyncio.subprocess.Process, input: bytes | None = None
) -> tuple[bytes, bytes]:
    """Like proc.communicate() but kills the whole process group on cancellation.

    Review tools can spawn child processes that outlive the direct subprocess
    if only the immediate child is killed. On cancellation we send SIGTERM to
    the process group, give it 3s to drain, then SIGKILL.
    Requires the subprocess to be started with start_new_session=True.
    """
    try:
        return await proc.communicate(input=input)
    except BaseException:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        raise


@dataclass
class PR:
    repo: str
    number: int
    github_org: str  # Each PR can have its own org

    @property
    def url(self) -> str:
        return f"https://github.com/{self.github_org}/{self.repo}/pull/{self.number}"

    @property
    def full_repo(self) -> str:
        return f"{self.github_org}/{self.repo}"


@dataclass
class ReviewResult:
    tool: str
    success: bool
    output: str
    error: str | None = None
    session_id: str | None = None
    duration_seconds: float | None = None


@dataclass
class ToolVersionInfo:
    github_repo: str  # e.g. "anthropics/claude-code"
    tag_prefix: str  # e.g. "v" or "rust-v" — stripped from tag to get version
    version_parse_re: str  # regex to extract version from --version output


TOOL_VERSION_INFO: dict[str, ToolVersionInfo] = {
    "claude": ToolVersionInfo(
        github_repo="anthropics/claude-code",
        tag_prefix="v",
        version_parse_re=r"([\d.]+)",
    ),
    "codex": ToolVersionInfo(
        github_repo="openai/codex",
        tag_prefix="rust-v",
        version_parse_re=r"([\d.]+)",
    ),
    "gemini": ToolVersionInfo(
        github_repo="google-gemini/gemini-cli",
        tag_prefix="v",
        version_parse_re=r"([\d.]+)",
    ),
}


def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string like '1.2.3' into a comparable tuple."""
    return tuple(int(x) for x in version_str.split("."))


async def check_tool_version(tool: str) -> tuple[str, str | None, str | None]:
    """Check installed vs latest version for a tool. Returns (tool, installed, latest)."""
    info = TOOL_VERSION_INFO.get(tool)
    if not info:
        return (tool, None, None)

    # Get installed version
    try:
        proc = await asyncio.create_subprocess_exec(
            tool, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        match = re.search(info.version_parse_re, stdout.decode())
        installed = match.group(1) if match else None
    except FileNotFoundError:
        installed = None

    # Get latest version from GitHub releases
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "release", "view",
            "--repo", info.github_repo,
            "--json", "tagName",
            "-q", ".tagName",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        tag = stdout.decode().strip()
        latest = tag.removeprefix(info.tag_prefix) if tag else None
    except FileNotFoundError:
        latest = None

    return (tool, installed, latest)


async def check_all_versions(tools: list[str]) -> list[str]:
    """Check versions for all tools, return list of warning messages."""
    results = await asyncio.gather(*(check_tool_version(t) for t in tools))
    warnings = []
    for tool, installed, latest in results:
        if not installed or not latest:
            continue
        try:
            if parse_version(installed) < parse_version(latest):
                warnings.append(f"  {tool}: {installed} -> {latest}")
        except ValueError:
            continue
    return warnings


def build_claude_prompt(prs: list[PR], review_dir: Path, project_root: Path) -> str:
    """Build the prompt for Claude Code."""
    pr_urls = " ".join(pr.url for pr in prs)
    output_path = review_dir / "FEEDBACK_claude.md"

    prompt = f"""/review {pr_urls}

Write your review to: {output_path}"""

    if output_path.exists():
        prompt += f"""

This is a RE-REVIEW. Your previous feedback is at: {output_path}
Read your previous feedback, re-review the PR, note what's been addressed and any new issues, then overwrite the file."""

    return prompt


def build_codex_prompt(prs: list[PR], review_dir: Path, project_root: Path) -> str:
    """Build the prompt for Codex."""
    pr_list = "\n".join(f"- {pr.full_repo} PR #{pr.number}: {pr.url}" for pr in prs)
    output_path = review_dir / "FEEDBACK_codex.md"

    # Build repo paths for context
    repo_paths = "\n".join(f"  - {project_root}/{pr.repo}" for pr in prs)

    prompt = f"""You are a senior software engineer conducting a thorough code review.

IMPORTANT CONSTRAINTS:
- Do NOT modify the filesystem (no checking out branches, no file changes) EXCEPT for writing your review
- You CAN view the repo clones at:
{repo_paths}

Review the following PR(s):
{pr_list}

Use the `gh` CLI to fetch PR details and diffs.
Focus on: bugs, security issues, logic errors, and performance problems.
Classify issues as: CRITICAL, HIGH, MEDIUM, or LOW.
Read related files for context - don't just analyze the diff in isolation.
Prefer no findings over marginal ones - only flag issues the author would likely fix.
Ignore trivial style violations unless they obscure meaning.
For bugs, specify the triggering conditions - explain when the issue occurs.

Write your review to: {output_path}"""

    if output_path.exists():
        prompt += f"""

This is a RE-REVIEW. Your previous feedback is at: {output_path}
Read your previous feedback, re-review the PR, note what's been addressed and any new issues, then overwrite the file."""

    return prompt


def build_gemini_prompt(prs: list[PR], review_dir: Path, project_root: Path) -> str:
    """Build the prompt for Gemini CLI."""
    pr_list = "\n".join(f"- {pr.repo} PR #{pr.number}: {pr.url}" for pr in prs)
    output_path = review_dir / "FEEDBACK_gemini.md"

    prompt = f"""You are a senior software engineer conducting a thorough code review.

Review the following PR(s):
{pr_list}

Use the `gh` CLI to fetch PR details and diffs.
Focus on: bugs, security issues, logic errors, and performance problems.
Classify issues as: CRITICAL, HIGH, MEDIUM, or LOW.
Read related files for context - don't just analyze the diff in isolation.
Prefer no findings over marginal ones - only flag issues the author would likely fix.
Ignore trivial style violations unless they obscure meaning.
For bugs, specify the triggering conditions - explain when the issue occurs.

Write your review to: {output_path}"""

    if output_path.exists():
        prompt += f"""

This is a RE-REVIEW. Your previous feedback is at: {output_path}
Read your previous feedback, re-review the PR, note what's been addressed and any new issues, then overwrite the file."""

    return prompt


async def run_claude(prompt: str, working_dir: Path) -> ReviewResult:
    """Run Claude Code review."""
    cmd = [
        "claude",
        "-p",
        "--dangerously-skip-permissions",
        "--allowedTools",
        "Bash,Read,Write,Glob,Grep",
        "--output-format",
        "json",
    ]

    # Allow nested Claude Code sessions by unsetting the guard variable
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=working_dir,
        env=env,
        start_new_session=True,
    )

    stdout, stderr = await _communicate_with_cleanup(proc, input=prompt.encode())
    output = stdout.decode()

    # Parse session_id from JSON output
    session_id = None
    if proc.returncode == 0:
        try:
            data = json.loads(output)
            session_id = data.get("session_id")
        except json.JSONDecodeError:
            pass

    return ReviewResult(
        tool="claude",
        success=proc.returncode == 0,
        output=output,
        error=stderr.decode() if proc.returncode != 0 else None,
        session_id=session_id,
    )


async def run_codex(prompt: str, working_dir: Path) -> ReviewResult:
    """Run Codex review."""
    cmd = [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--json",
        prompt,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=working_dir,
        start_new_session=True,
    )

    stdout, stderr = await _communicate_with_cleanup(proc)
    output = stdout.decode()

    # Parse thread_id from first JSON line (thread.started event)
    session_id = None
    if proc.returncode == 0:
        for line in output.splitlines():
            try:
                data = json.loads(line)
                if data.get("type") == "thread.started":
                    session_id = data.get("thread_id")
                    break
            except json.JSONDecodeError:
                continue

    return ReviewResult(
        tool="codex",
        success=proc.returncode == 0,
        output=output,
        error=stderr.decode() if proc.returncode != 0 else None,
        session_id=session_id,
    )


async def run_gemini(prompt: str, working_dir: Path) -> ReviewResult:
    """Run Gemini CLI review."""
    cmd = [
        "gemini",
        "--yolo",
        "-m",
        "gemini-3-flash-preview",
        "--output-format",
        "json",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=working_dir,
        start_new_session=True,
    )

    stdout, stderr = await _communicate_with_cleanup(proc, input=prompt.encode())
    output = stdout.decode()

    # Parse session_id from JSON output
    session_id = None
    if proc.returncode == 0:
        try:
            data = json.loads(output)
            session_id = data.get("session_id")
        except json.JSONDecodeError:
            pass

    return ReviewResult(
        tool="gemini",
        success=proc.returncode == 0,
        output=output,
        error=stderr.decode() if proc.returncode != 0 else None,
        session_id=session_id,
    )


async def run_with_timeout(
    coro, tool_name: str, timeout_seconds: int = 480
) -> ReviewResult:
    """Run a coroutine with a timeout."""
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        result.duration_seconds = round(time.monotonic() - start, 2)
        return result
    except asyncio.TimeoutError:
        return ReviewResult(
            tool=tool_name,
            success=False,
            output="",
            error=f"Timeout after {timeout_seconds} seconds",
            duration_seconds=float(timeout_seconds),
        )


DEFAULT_TOOLS = ["claude", "codex", "gemini"]

TOOL_RUNNERS = {
    "claude": (run_claude, build_claude_prompt),
    "codex": (run_codex, build_codex_prompt),
    "gemini": (run_gemini, build_gemini_prompt),
}


async def run_reviews(
    prs: list[PR],
    review_dir: Path,
    working_dir: Path,
    project_root: Path,
    tools: list[str],
    timeout_seconds: int = 480,
) -> list[ReviewResult]:
    """Run reviews in parallel for specified tools."""
    # Create tasks for requested tools
    tasks = []
    for tool in tools:
        runner, prompt_builder = TOOL_RUNNERS[tool]
        prompt = prompt_builder(prs, review_dir, project_root)
        coro = runner(prompt, working_dir)
        tasks.append(run_with_timeout(coro, tool, timeout_seconds))

    # Print status
    print("Starting reviews...")
    for tool in tools:
        print(f"  {tool.capitalize():10} running", flush=True)

    # Run version checks concurrently with reviews
    version_task = asyncio.create_task(check_all_versions(tools))

    # Run all in parallel and collect results as they complete
    results: list[ReviewResult] = []
    for coro in asyncio.as_completed(tasks):
        result = await coro

        # Verify the tool actually wrote its feedback file. A tool can exit 0
        # without calling any write tool (seen with some model/CLI combos),
        # so trusting the return code alone produces phantom successes.
        if result.success:
            expected = review_dir / f"FEEDBACK_{result.tool}.md"
            if not expected.exists():
                result.success = False
                snippet = (result.output or "").strip()[:500]
                result.error = (
                    f"No {expected.name} written. "
                    f"Tail of stdout: {snippet!r}" if snippet
                    else f"No {expected.name} written and stdout was empty."
                )

        results.append(result)
        status = "done" if result.success else f"FAILED: {result.error}"
        print(f"  {result.tool.capitalize():10} {status}", flush=True)

    # Print version warnings if any
    version_warnings = await version_task
    if version_warnings:
        print("\nUpdate(s) available:")
        for warning in version_warnings:
            print(warning)

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PR reviews in parallel")
    parser.add_argument(
        "--review-dir",
        type=Path,
        required=True,
        help="Directory to write review files to",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        required=True,
        help="Project root directory (contains repos and config)",
    )

    # Two ways to specify PRs: JSON (from preflight) or manual args
    parser.add_argument(
        "--prs-json",
        type=str,
        help="JSON array of PR objects with repo, number, github_org fields",
    )
    parser.add_argument(
        "--repo",
        action="append",
        dest="repos",
        help="Repository name (can be specified multiple times)",
    )
    parser.add_argument(
        "--pr",
        action="append",
        dest="prs",
        type=int,
        help="PR number (can be specified multiple times, must match --repo order)",
    )
    parser.add_argument(
        "--github-org",
        type=str,
        help="GitHub organization (used with --repo/--pr, same for all PRs)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=480,
        help="Timeout in seconds per tool (default: 480)",
    )
    parser.add_argument(
        "--tools",
        type=str,
        default=None,
        help="Comma-separated list of tools to run (default: claude,codex,gemini)",
    )
    args = parser.parse_args()

    # Validate PR specification
    if args.prs_json:
        # PRs specified via JSON
        pass
    elif args.repos and args.prs:
        # PRs specified via --repo/--pr args
        if len(args.repos) != len(args.prs):
            parser.error("Number of --repo and --pr arguments must match")
        if not args.github_org:
            parser.error("--github-org required when using --repo/--pr")
    else:
        parser.error("Must specify PRs via --prs-json OR --repo/--pr/--github-org")

    # Parse tools list
    all_tools = list(TOOL_RUNNERS.keys())
    if args.tools:
        args.tools = [t.strip().lower() for t in args.tools.split(",")]
        invalid = [t for t in args.tools if t not in all_tools]
        if invalid:
            parser.error(f"Invalid tools: {invalid}. Valid options: {all_tools}")
    else:
        args.tools = DEFAULT_TOOLS.copy()

    return args


def main() -> int:
    args = parse_args()

    # Build PR list from either JSON or args
    if args.prs_json:
        pr_data = json.loads(args.prs_json)
        prs = [
            PR(repo=p["repo"], number=p["number"], github_org=p["github_org"])
            for p in pr_data
        ]
    else:
        prs = [
            PR(repo=repo, number=pr, github_org=args.github_org)
            for repo, pr in zip(args.repos, args.prs)
        ]

    # Ensure review directory exists
    args.review_dir.mkdir(parents=True, exist_ok=True)

    # Run reviews
    results = asyncio.run(
        run_reviews(
            prs=prs,
            review_dir=args.review_dir,
            working_dir=args.project_root,
            project_root=args.project_root,
            tools=args.tools,
            timeout_seconds=args.timeout,
        )
    )

    # Save session IDs to metadata.json
    metadata_path = args.review_dir / "metadata.json"
    metadata = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text())
        except json.JSONDecodeError:
            pass

    if "sessions" not in metadata:
        metadata["sessions"] = {}
    if "durations" not in metadata:
        metadata["durations"] = {}

    for result in results:
        if result.session_id:
            metadata["sessions"][result.tool] = result.session_id
        if result.duration_seconds is not None:
            metadata["durations"][result.tool] = result.duration_seconds

    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")

    # Summary
    print("\n" + "=" * 40)
    succeeded = sum(1 for r in results if r.success)
    total = len(args.tools)
    print(f"Reviews completed: {succeeded}/{total}")

    for result in results:
        if not result.success:
            print(f"\n{result.tool} failed: {result.error}")

    # Return non-zero if any failed
    return 0 if all(r.success for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
