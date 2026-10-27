#!/usr/bin/env python3
"""Automated Antigravity Code Reviewer Hook.

Triggered on the 'Stop' lifecycle event before the agent turn finishes.
Leverages the Google AI Studio Antigravity Agent (antigravity-preview-09-2026)
running Gemini 3.8 Flash (gemini-3.8-flash) via the Interactions API.
Fails open on quota limits (HTTP 429), timeouts, or API errors to avoid blocking the user.
"""

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def log_err(msg: str) -> None:
    sys.stderr.write(f"[code-reviewer] {msg}\n")
    sys.stderr.flush()


def output_decision(decision: str, reason: str = "") -> None:
    result: dict[str, str] = {"decision": decision}
    if reason:
        result["reason"] = reason
    print(json.dumps(result))
    sys.stdout.flush()


def find_api_key(workspace_dir: Path) -> str | None:
    """Find GEMINI_API_KEY or GOOGLE_API_KEY from environment or local .env files."""
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()

    candidate_paths = [
        workspace_dir / ".env",
        Path(__file__).resolve().parent.parent / ".env",
        Path.home() / ".gemini" / ".env",
    ]
    for env_path in candidate_paths:
        if env_path.is_file():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("\"'")
                        if k in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and v:
                            return v
            except Exception as e:
                log_err(f"Failed reading {env_path}: {e}")

    return None


def get_git_diff(workspace_dir: Path) -> str:
    """Fetch uncommitted changes across staged, unstaged, and untracked files."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workspace_dir),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return ""

        diff_res = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(workspace_dir),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        diff_text = diff_res.stdout
        if not diff_text.strip():
            d1 = subprocess.run(
                ["git", "diff"],
                cwd=str(workspace_dir),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
            d2 = subprocess.run(
                ["git", "diff", "--cached"],
                cwd=str(workspace_dir),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
            diff_text = d1 + "\n" + d2

        # Include untracked new files
        untracked_chunks: list[str] = []
        for line in res.stdout.splitlines():
            if line.startswith("?? "):
                rel_path = line[3:].strip().strip('"')
                target = workspace_dir / rel_path
                # Skip large build/cache directories
                if target.is_file() and not rel_path.startswith(
                    (".git", "node_modules", ".venv", "scratch", ".gemini")
                ):
                    try:
                        content = target.read_text(encoding="utf-8", errors="replace")
                        added_lines = "\n".join(f"+{item}" for item in content.splitlines())
                        untracked_chunks.append(
                            f"diff --git a/{rel_path} b/{rel_path}\n"
                            f"new file mode 100644\n"
                            f"--- /dev/null\n"
                            f"+++ b/{rel_path}\n"
                            f"@@ -0,0 +1 @@\n{added_lines}"
                        )
                    except Exception:
                        pass

        if untracked_chunks:
            diff_text = (diff_text + "\n" + "\n".join(untracked_chunks)).strip()

        return diff_text.strip()
    except Exception as e:
        log_err(f"Git diff error: {e}")
        return ""


def main() -> None:
    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception as e:
        log_err(f"Failed to parse stdin payload: {e}")
        output_decision("allow")
        return

    # Only run review on clean completion (NO_TOOL_CALL)
    termination_reason = payload.get("terminationReason", "")
    if termination_reason and termination_reason != "NO_TOOL_CALL":
        output_decision("allow")
        return

    # Prevent loop if executionNum exceeds threshold
    execution_num = payload.get("executionNum", 1)
    if execution_num > 2:
        log_err(f"Execution count {execution_num} > 2. Allowing completion to prevent loops.")
        output_decision("allow")
        return

    workspace_paths = payload.get("workspacePaths", [])
    if workspace_paths and os.path.exists(workspace_paths[0]):
        workspace_dir = Path(workspace_paths[0]).resolve()
    else:
        workspace_dir = Path.cwd().resolve()

    diff_text = get_git_diff(workspace_dir)
    if not diff_text:
        output_decision("allow")
        return

    # Prevent duplicate reviews on identical diff
    diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    cache_file = Path(__file__).resolve().parent.parent / ".last_review_hash"
    if cache_file.is_file():
        try:
            last_hash = cache_file.read_text(encoding="utf-8").strip()
            if last_hash == diff_hash:
                log_err("Diff unchanged since last review. Allowing completion.")
                output_decision("allow")
                return
        except Exception:
            pass

    try:
        cache_file.write_text(diff_hash, encoding="utf-8")
    except Exception:
        pass

    api_key = find_api_key(workspace_dir)
    if not api_key:
        log_err("No GEMINI_API_KEY or GOOGLE_API_KEY found. Skipping review.")
        output_decision("allow")
        return

    # Truncate diff if abnormally large to respect context & quota
    max_chars = 60000
    if len(diff_text) > max_chars:
        diff_text = diff_text[:max_chars] + f"\n\n[Diff truncated: exceeded {max_chars} chars]"

    # Use Google AI Studio Antigravity agent (defaults to Gemini 3.8 Flash)
    agent_name = os.environ.get("ANTIGRAVITY_REVIEW_AGENT")
    if not agent_name or agent_name in ("1", "true", "True", "0", "false", "False"):
        agent_name = "antigravity-preview-09-2026"
    url = f"https://generativelanguage.googleapis.com/v1beta/interactions?key={api_key}"

    prompt = (
        "You are an expert, strict senior software engineer doing an automated pre-finish review.\n"
        "Review the uncommitted git diff below.\n\n"
        "Checklist:\n"
        "1. Logic bugs, undefined references, race conditions, or null pointers.\n"
        "2. Security issues or exposed secrets.\n"
        "3. Unhandled error boundaries or resource leaks.\n"
        "4. Non-trivial logic without verification or tests.\n\n"
        "Instructions:\n"
        "- If NO severe defects exist, respond with EXACTLY the single word: PASSED\n"
        "- If actionable defects exist, list them concisely (file, line, suggested fix).\n\n"
        f"--- GIT DIFF ---\n{diff_text}"
    )

    req_body = json.dumps({
        "agent": agent_name,
        "input": prompt,
        "environment": "remote",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=req_body,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))

            review_text = data.get("output_text", "")
            if not review_text:
                parts = []
                for step in data.get("steps", []):
                    if step.get("type") == "model_output":
                        for item in step.get("content", []):
                            if item.get("type") == "text":
                                parts.append(item.get("text", ""))
                review_text = "\n".join(parts).strip()

            if not review_text or "PASSED" in review_text[:20].upper():
                log_err("Code review passed.")
                output_decision("allow")
            else:
                log_err("Code review reported issues. Requesting continuation.")
                reason_msg = (
                    f"Antigravity Agent (Gemini 3.8 Flash) detected issues before completion:\n\n"
                    f"{review_text}\n\n"
                    f"Please review and resolve these issues before completing the task."
                )
                output_decision(decision="continue", reason=reason_msg)

    except urllib.error.HTTPError as e:
        # Gracefully fail open on HTTP 429 (quota), 403, 503, etc.
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        log_err(
            f"Gemini API returned HTTP {e.code} ({e.reason}): {err_body}. Failing open."
        )
        output_decision("allow")
    except urllib.error.URLError as e:
        log_err(f"Network error contacting Gemini API: {e.reason}. Failing open.")
        output_decision("allow")
    except Exception as e:
        log_err(f"Unexpected error during code review: {e}. Failing open.")
        output_decision("allow")


if __name__ == "__main__":
    main()
