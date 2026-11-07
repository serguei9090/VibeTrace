"""Export script to compile the complete multi-language quality rules catalog.

Connects to the local SonarQube Community server or external feeds, fetches rule
specifications across Python, TypeScript, JavaScript, CSS, and Docker, and compiles
them into a compressed JSON manifest.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_SONAR_URL = os.getenv("SONAR_URL", "http://localhost:9000")
DEFAULT_SONAR_TOKEN = os.getenv("SONAR_TOKEN", "")
TARGET_LANGS = "py,ts,js,css,docker"


def clean_html(text: str) -> str:
    """Strips HTML tags and normalizes whitespace."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = html.unescape(clean)
    return " ".join(clean.split())


def extract_cwes(text: str, tags: list[str]) -> list[str]:
    """Extracts normalized CWE IDs ('CWE-89') from descriptions and tags."""
    combined = f"{text} {' '.join(tags)}"
    matches = re.findall(r"(?i)\bcwe[-_]?(\d+)\b", combined)
    found: list[str] = []
    for m in matches:
        cid = f"CWE-{m}"
        if cid not in found:
            found.append(cid)
    return found


def fetch_ruff_rules() -> dict[str, dict[str, Any]]:
    """Extracts all 900+ Ruff rule specifications via `ruff rule --all --output-format json`."""
    print("Exporting Ruff rule dictionary...")
    for cmd in (
        ["ruff", "rule", "--all", "--output-format", "json"],
        ["uv", "run", "ruff", "rule", "--all", "--output-format", "json"],
    ):
        try:
            res = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace", check=True
            )
            loaded = json.loads(res.stdout)
            if isinstance(loaded, list):
                ruff_dict: dict[str, dict[str, Any]] = {}
                for r in loaded:
                    code = r.get("code")
                    if code:
                        ruff_dict[code.upper()] = {
                            "code": code,
                            "name": r.get("name", code),
                            "linter": r.get("linter", "ruff"),
                            "summary": r.get("summary", ""),
                            "fix": r.get("fix_availability", "None"),
                        }
                print(f"[SUCCESS] Exported {len(ruff_dict)} Ruff rules into dictionary.")
                return ruff_dict
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            continue
    print("[WARNING] Could not execute ruff command; proceeding without local Ruff dump.")
    return {}


def fetch_all_sonar_rules(
    server_url: str = DEFAULT_SONAR_URL,
    token: str = DEFAULT_SONAR_TOKEN,
    languages: str | None = None,
) -> list[dict[str, Any]]:
    """Fetches all rules across target languages using paginated SonarQube REST API.

    If languages is None or 'all', fetches rules for all languages supported by the server.
    """
    auth_header = f"Basic {base64.b64encode(f'{token}:'.encode('ascii')).decode('ascii')}"
    all_rules: list[dict[str, Any]] = []
    page = 1
    page_size = 500

    lang_desc = "all languages" if not languages or languages.lower() == "all" else languages
    print(f"Connecting to SonarQube at {server_url} for {lang_desc}...")
    while True:
        lang_query = (
            "" if not languages or languages.lower() == "all" else f"languages={languages}&"
        )
        url = f"{server_url.rstrip('/')}/api/rules/search?{lang_query}ps={page_size}&p={page}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", auth_header)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            print(f"Error fetching rules from {url}: {e}", file=sys.stderr)
            raise

        rules = data.get("rules", [])
        if not rules:
            break

        all_rules.extend(rules)
        total = data.get("total", len(all_rules))
        print(f"  Fetched page {page} ({len(all_rules)}/{total} rules)...")

        if len(all_rules) >= total:
            break
        page += 1

    return all_rules


def compile_rules_manifest(raw_rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Transforms raw SonarQube rule payloads into a compact, token-efficient manifest."""
    compiled: list[dict[str, Any]] = []

    for r in raw_rules:
        # Extract explanation text from description sections
        desc_parts: list[str] = []
        for sec in r.get("descriptionSections", []):
            if sec.get("key") in ("root_cause", "introduction", "how_to_fix"):
                cleaned = clean_html(sec.get("content", ""))
                if cleaned:
                    desc_parts.append(cleaned)

        desc = " ".join(desc_parts)[:500].strip()

        # Extract parameters (e.g. threshold = 15)
        params: dict[str, Any] = {}
        for p in r.get("params", []):
            pkey = p.get("key")
            pval = p.get("defaultValue")
            if pkey and pval is not None:
                params[pkey] = pval

        tags = r.get("sysTags", []) + r.get("tags", [])
        desc_secs = r.get("descriptionSections", [])
        all_desc_text = " ".join([sec.get("content", "") for sec in desc_secs])
        cwes = extract_cwes(all_desc_text, tags)

        compiled.append(
            {
                "key": r.get("key"),
                "name": r.get("name"),
                "lang": r.get("lang"),
                "severity": r.get("severity", "MAJOR"),
                "type": r.get("type", "CODE_SMELL"),
                "tags": tags,
                "cwes": cwes if cwes else None,
                "desc": desc,
                "params": params if params else None,
            }
        )

    ruff_rules = fetch_ruff_rules()

    return {
        "version": "1.1",
        "generated_at": int(time.time()),
        "total_rules": len(compiled),
        "total_ruff_rules": len(ruff_rules),
        "languages": sorted({r["lang"] for r in compiled}),
        "rules": compiled,
        "ruff_rules": ruff_rules,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export SonarQube Quality Rules to JSON manifest")
    parser.add_argument("--url", default=DEFAULT_SONAR_URL, help="SonarQube Server URL")
    parser.add_argument("--token", default=DEFAULT_SONAR_TOKEN, help="SonarQube Auth Token")
    parser.add_argument(
        "--langs",
        default="all",
        help="Comma-separated language keys, or 'all' to export all supported languages",
    )
    parser.add_argument(
        "--output",
        default="src/vibetrace/resources/quality_rules_catalog.json",
        help="Target output JSON path",
    )
    parser.add_argument(
        "--gzip",
        action="store_true",
        help="Also write a .gz compressed copy",
    )
    args = parser.parse_args()

    raw_rules = fetch_all_sonar_rules(server_url=args.url, token=args.token, languages=args.langs)
    if not raw_rules:
        print("No rules fetched. Aborting.", file=sys.stderr)
        return 1

    manifest = compile_rules_manifest(raw_rules)
    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    json_text = json.dumps(manifest, indent=2, ensure_ascii=False)
    out_path.write_text(json_text, encoding="utf-8")
    print(
        f"[SUCCESS] Exported {manifest['total_rules']} rules to `{out_path}` "
        f"({len(json_text.encode('utf-8')) / 1024:.1f} KB)"
    )

    if args.gzip:
        gz_path = out_path.with_suffix(".json.gz")
        with gzip.open(gz_path, "wb") as f:
            f.write(json.dumps(manifest, separators=(",", ":")).encode("utf-8"))
        gz_size_kb = gz_path.stat().st_size / 1024
        print(f"[PACKAGE] Compressed gzip manifest written to `{gz_path}` ({gz_size_kb:.1f} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
