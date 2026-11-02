"""CLI command handler for vibetrace tools (diagnostics and sub-engine provisioning)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vibetrace.tools_manager import ToolsManager


def cmd_tools(args: argparse.Namespace) -> int:
    """Manages language tools and quality sub-engines."""
    action = getattr(args, "action", "status") or "status"
    root = Path(getattr(args, "path", ".") or ".").resolve()

    if action in ("status", "doctor", "list"):
        statuses = ToolsManager.get_status(root)
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        k: {
                            "language": v.language,
                            "engine": v.engine,
                            "is_available": v.is_available,
                            "version_or_path": v.version_or_path,
                            "install_hint": v.install_hint,
                            "auto_installable": v.auto_installable,
                        }
                        for k, v in statuses.items()
                    },
                    indent=2,
                )
            )
            return 0

        print("\n🔧 VibeTrace Polyglot Sub-Engine Status:")
        print("=" * 60)
        for lang, s in statuses.items():
            icon = "✅" if s.is_available else "❌"
            status_text = "READY" if s.is_available else "NOT INSTALLED"
            print(f"{icon} {lang.upper():<12} [{status_text}] - {s.engine}")
            if s.is_available and s.version_or_path:
                print(f"   Path: {s.version_or_path}")
            elif not s.is_available:
                print(f"   Install: {s.install_hint}")
        print("=" * 60)
        print("Run 'vibetrace tools install <language>' to provision missing engines.\n")
        return 0

    if action == "install":
        lang = getattr(args, "language", "") or ""
        if not lang:
            print("❌ Please specify a language to install (e.g. 'vibetrace tools install php')")
            return 1

        languages = (
            ["python", "typescript", "go", "rust", "php", "ruby", "shell", "sql"]
            if lang.lower() == "all"
            else [lang]
        )

        all_ok = True
        for target_lang in languages:
            print(f"📦 Provisioning quality engine for {target_lang}...")
            ok, msg = ToolsManager.install(target_lang, root)
            if ok:
                print(f"  ✅ {msg}")
            else:
                print(f"  ❌ {msg}")
                all_ok = False

        return 0 if all_ok else 1

    print(f"❌ Unknown tools action: {action}")
    return 1
