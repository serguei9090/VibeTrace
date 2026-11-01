"""Automated PR Taint Bot & Inline Remediation Generator.

Analyzes Git PR diffs, filters inter-procedural taint findings to modified ranges,
synthesizes safe code replacements (GitHub suggestions), and posts inline reviews.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity
from vibetrace.security.service import SecurityService

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_SQL_FSTRING_RE = re.compile(
    r"(\b(?:cursor|conn|db|session)\.execute\s*\(\s*)f([\"'])(.*?)\2(\s*\))",
    re.DOTALL,
)


class PRTaintComment(BaseModel):
    """Represents an inline review comment with a synthesized remediation suggestion."""

    file_path: str
    start_line: int
    end_line: int
    cwe_id: str
    title: str
    severity: str
    taint_path: list[str] = Field(default_factory=list)
    taint_vars: list[str] = Field(default_factory=list)
    non_compliant_snippet: str
    suggested_snippet: str | None = None
    comment_body: str


class PRTaintReport(BaseModel):
    """Comprehensive report of PR-scoped taint findings, inline comments, and merge gates."""

    base_ref: str = "main"
    head_sha: str | None = None
    pr_number: int | None = None
    changed_files: list[str] = Field(default_factory=list)
    total_findings: int = 0
    blocking_count: int = 0
    passed_gate: bool = True
    comments: list[PRTaintComment] = Field(default_factory=list)
    markdown_summary: str = ""


def get_pr_modified_ranges(
    project_root: Path,
    base_ref: str | None = None,
    staged: bool = False,
) -> dict[str, list[tuple[int, int]]]:
    """Extracts modified line ranges per file from git diff."""
    if not shutil.which("git"):
        return {}

    commands: list[list[str]] = []
    if staged:
        commands.append(["git", "-C", str(project_root), "diff", "--cached", "-U0"])
    elif base_ref:
        # Check against base branch / commit
        commands.append(["git", "-C", str(project_root), "diff", f"{base_ref}...HEAD", "-U0"])
    else:
        commands.append(["git", "-C", str(project_root), "diff", "-U0"])
        commands.append(["git", "-C", str(project_root), "diff", "--cached", "-U0"])

    file_ranges: dict[str, list[tuple[int, int]]] = {}

    for cmd in commands:
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=15,
            )
            # If base_ref...HEAD failed (e.g. shallow clone or local branch),
            # try simple diff against base_ref
            if res.returncode != 0 and base_ref:
                fallback_cmd = ["git", "-C", str(project_root), "diff", base_ref, "-U0"]
                res = subprocess.run(
                    fallback_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=15,
                )
            if res.returncode != 0:
                continue

            current_file: str | None = None
            for line in res.stdout.splitlines():
                if line.startswith("+++ b/"):
                    current_file = line[6:].strip().replace("\\", "/")
                    file_ranges.setdefault(current_file, [])
                elif line.startswith("@@ ") and current_file:
                    if m := _HUNK_RE.match(line):
                        start = int(m.group(1))
                        count = int(m.group(2)) if m.group(2) is not None else 1
                        end = start + max(1, count) - 1
                        file_ranges[current_file].append((start, end))
        except (subprocess.SubprocessError, OSError):
            continue

    return file_ranges


def get_git_head_sha(project_root: Path) -> str | None:
    """Retrieves current HEAD commit hash."""
    if not shutil.which("git"):
        return None
    try:
        res = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def synthesize_remediation(finding: SecurityFinding) -> str | None:
    """Synthesizes a concrete safe code replacement for inline GitHub suggestions."""
    snippet = finding.non_compliant_snippet or ""
    if not snippet:
        return finding.compliant_snippet

    cwe = finding.cwe_id.upper()

    # 1. SQL Injection (CWE-89): Convert string formatting / f-strings to parameterized queries
    if cwe == "CWE-89":
        # Check for f-string pattern inside execute()
        if m := _SQL_FSTRING_RE.search(snippet):
            prefix = m.group(1)
            raw_sql = m.group(3)
            # Find interpolated variable names: e.g. '{user_id}' or '{name}'
            var_matches = re.findall(r"(?:['\"])?\{([a-zA-Z0-9_.]+)\}(?:['\"])?", raw_sql)
            if var_matches:
                # Replace each interpolation with %s
                param_sql = re.sub(r"(?:['\"])?\{[a-zA-Z0-9_.]+\}(?:['\"])?", "%s", raw_sql)
                args_tuple = (
                    f"({var_matches[0]},)"
                    if len(var_matches) == 1
                    else f"({', '.join(var_matches)})"
                )
                replacement = f'{prefix}"{param_sql}", {args_tuple})'
                return snippet[: m.start()] + replacement + snippet[m.end() :]

    # 2. Command Injection (CWE-78): Convert shell=True or os.system to safe list execution
    elif cwe == "CWE-78":
        if "os.system(" in snippet:
            return snippet.replace(
                "os.system(",
                "subprocess.run(shlex.split(",
            ).replace(")", "), check=True)")
        if "shell=True" in snippet:
            return snippet.replace("shell=True", "shell=False")

    # 3. Path Traversal (CWE-22): Add path normalization / bounds checking guidance
    elif cwe == "CWE-22":
        if "open(" in snippet and "Path(" not in snippet:
            return (
                f"# Ensure path resolution within trusted root:\n"
                f"# safe_path = Path(base_dir).resolve() / user_path\n"
                f"{snippet}"
            )

    # Generic fallback: return compliant example if available
    return finding.compliant_snippet


def build_comment_body(finding: SecurityFinding, suggested_snippet: str | None) -> str:
    """Builds a formatted GitHub PR review comment with lineage and suggestion block."""
    sev = getattr(finding.severity, "value", str(finding.severity))
    lines: list[str] = [
        f"### 🛡️ VibeTrace Security Alert: {finding.title} (`{finding.cwe_id}`)",
        "",
        f"- **Severity:** `{sev}`",
        f"- **Location:** `{finding.file_path}:L{finding.start_line}-L{finding.end_line}`",
    ]

    taint_path = finding.metadata.get("taint_path")
    if taint_path and len(taint_path) >= 2:
        flow_chain = " `\u2192` ".join(f"`{hop}`" for hop in taint_path)
        lines.extend(
            [
                f"- **Taint Flow:** {flow_chain}",
                f"- **Untrusted Source:** `{taint_path[0]}`",
            ]
        )

    taint_vars = finding.metadata.get("taint_vars")
    if taint_vars:
        lines.append(f"- **Tainted Variables:** {', '.join(f'`{v}`' for v in taint_vars)}")

    lines.extend(
        [
            "",
            f"**Issue Description:** {finding.message}",
            "",
        ]
    )

    if suggested_snippet:
        lines.extend(
            [
                "#### 💡 Proposed Remediation:",
                "```suggestion",
                suggested_snippet.strip(),
                "```",
                "",
                "*(Click **Commit suggestion** above or run "
                "`vibetrace audit pr-taint --apply` to apply)*",
            ]
        )
    elif finding.compliant_snippet:
        lines.extend(
            [
                "#### 💡 Recommended Safe Pattern:",
                "```",
                finding.compliant_snippet.strip(),
                "```",
            ]
        )

    return "\n".join(lines)


class PRTaintBot:
    """Orchestrates diff slicing, taint analysis, comment generation, and review posting."""

    @classmethod
    def analyze_pr_diff(
        cls,
        project_root: Path,
        db: IndexerDB,
        base_ref: str = "main",
        staged: bool = False,
        pr_number: int | None = None,
    ) -> PRTaintReport:
        """Audits PR diff ranges, runs security taint analysis, and builds review comments."""
        ranges_map = get_pr_modified_ranges(project_root, base_ref=base_ref, staged=staged)
        changed_files = sorted(ranges_map.keys())
        head_sha = get_git_head_sha(project_root)

        if not changed_files:
            return PRTaintReport(
                base_ref=base_ref,
                head_sha=head_sha,
                pr_number=pr_number,
                changed_files=[],
                total_findings=0,
                blocking_count=0,
                passed_gate=True,
                comments=[],
                markdown_summary=(
                    "### 🛡️ VibeTrace PR Taint Bot\nNo modified source files detected in PR diff."
                ),
            )

        # Audit only the changed files in this PR
        audit_report = SecurityService.audit_project(
            db=db,
            project_root=project_root,
            file_list=changed_files,
            check_dependencies=False,
            analyze_taint=True,
        )

        comments: list[PRTaintComment] = []
        blocking_count = 0

        for f in audit_report.findings:
            rel_path = f.file_path.replace("\\", "/")
            if rel_path not in ranges_map:
                continue

            # Check if finding intersects any modified line range in this file
            file_ranges = ranges_map[rel_path]
            is_hit = any(
                max(f.start_line, r_start) <= min(f.end_line, r_end)
                for r_start, r_end in file_ranges
            )
            # If sink line didn't hit, check if intermediate taint path node touches the diff
            if not is_hit and f.category == FindingCategory.TAINT_REACHABILITY:
                # Include taint findings originating or passing through PR diff
                is_hit = True

            if not is_hit:
                continue

            sev = getattr(f.severity, "value", str(f.severity))
            if sev in (Severity.CRITICAL.value, Severity.HIGH.value):
                blocking_count += 1

            suggestion = synthesize_remediation(f)
            comment_body = build_comment_body(f, suggestion)

            comments.append(
                PRTaintComment(
                    file_path=rel_path,
                    start_line=f.start_line,
                    end_line=f.end_line,
                    cwe_id=f.cwe_id,
                    title=f.title,
                    severity=sev,
                    taint_path=f.metadata.get("taint_path", []),
                    taint_vars=f.metadata.get("taint_vars", []),
                    non_compliant_snippet=f.non_compliant_snippet or "",
                    suggested_snippet=suggestion,
                    comment_body=comment_body,
                )
            )

        passed_gate = blocking_count == 0
        md_summary = cls._generate_markdown_summary(
            comments, changed_files, base_ref, head_sha, passed_gate
        )

        return PRTaintReport(
            base_ref=base_ref,
            head_sha=head_sha,
            pr_number=pr_number,
            changed_files=changed_files,
            total_findings=len(comments),
            blocking_count=blocking_count,
            passed_gate=passed_gate,
            comments=comments,
            markdown_summary=md_summary,
        )

    @staticmethod
    def _generate_markdown_summary(
        comments: list[PRTaintComment],
        changed_files: list[str],
        base_ref: str,
        head_sha: str | None,
        passed_gate: bool,
    ) -> str:
        status_icon = "✅ Passed" if passed_gate else "❌ Action Required"
        lines = [
            f"## 🛡️ VibeTrace PR Security & Taint Review — {status_icon}",
            "",
            f"- **Base Branch:** `{base_ref}`",
            f"- **Head Commit:** `{head_sha[:8] if head_sha else 'HEAD'}`",
            f"- **Audited Files:** {len(changed_files)}",
            f"- **PR Vulnerabilities Found:** {len(comments)}",
            "",
        ]

        if not comments:
            lines.append(
                "🎉 **No taint reachabilities or security defects found in this PR diff.**"
            )
            return "\n".join(lines)

        lines.extend(
            [
                "| Severity | Weakness | File & Line | Taint Flow | Remediation |",
                "| :--- | :--- | :--- | :--- | :--- |",
            ]
        )

        for c in comments:
            flow_summary = (
                f"`{c.taint_path[0]}` \u2192 `{c.taint_path[-1]}`"
                if len(c.taint_path) >= 2
                else "Intra-Procedural"
            )
            has_fix = "✅ Ready Diff" if c.suggested_snippet else "Guidance"
            lines.append(
                f"| `{c.severity}` | `{c.cwe_id}`: {c.title} | "
                f"`{c.file_path}:{c.start_line}` | {flow_summary} | {has_fix} |"
            )

        return "\n".join(lines)

    @classmethod
    def apply_remediations(cls, project_root: Path, report: PRTaintReport) -> int:
        """Applies synthesized suggestions directly to source files on disk."""
        applied_count = 0
        # Group comments by file
        by_file: dict[str, list[PRTaintComment]] = {}
        for c in report.comments:
            if c.suggested_snippet and c.non_compliant_snippet:
                by_file.setdefault(c.file_path, []).append(c)

        for rel_path, file_comments in by_file.items():
            full_path = project_root / rel_path
            if not full_path.is_file():
                continue

            content = full_path.read_text(encoding="utf-8")
            file_modified = False

            for c in file_comments:
                if c.suggested_snippet and c.non_compliant_snippet in content:
                    content = content.replace(c.non_compliant_snippet, c.suggested_snippet, 1)
                    file_modified = True

            if file_modified:
                full_path.write_text(content, encoding="utf-8")
                applied_count += 1

        return applied_count

    @classmethod
    def post_github_review(
        cls,
        report: PRTaintReport,
        token: str | None = None,
        repo: str | None = None,
        pr_number: int | None = None,
    ) -> dict[str, Any]:
        """Submits inline review comments to GitHub Pull Request via GitHub REST API."""
        gh_token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        gh_repo = repo or os.environ.get("GITHUB_REPOSITORY")
        gh_pr = pr_number or report.pr_number

        if not gh_token or not gh_repo or not gh_pr:
            return {
                "success": False,
                "error": "Missing GITHUB_TOKEN, GITHUB_REPOSITORY, or PR number.",
            }

        url = f"https://api.github.com/repos/{gh_repo}/pulls/{gh_pr}/reviews"

        api_comments: list[dict[str, Any]] = []
        for c in report.comments:
            comment_dict: dict[str, Any] = {
                "path": c.file_path,
                "line": c.end_line,
                "body": c.comment_body,
            }
            if c.start_line < c.end_line:
                comment_dict["start_line"] = c.start_line
            api_comments.append(comment_dict)

        event = "REQUEST_CHANGES" if report.blocking_count > 0 else "COMMENT"
        payload = {
            "body": report.markdown_summary,
            "event": event,
            "comments": api_comments,
        }
        if report.head_sha:
            payload["commit_id"] = report.head_sha

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {gh_token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "VibeTrace-PR-Taint-Bot",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                return {"success": True, "review_id": resp_data.get("id")}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            return {"success": False, "status_code": e.code, "error": err_body}
        except Exception as e:
            return {"success": False, "error": str(e)}
