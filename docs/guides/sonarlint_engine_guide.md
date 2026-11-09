# SonarLint Headless LSP Engine & Polyglot Quality Architecture

This guide explains VibeTrace's two quality architecture families, the headless SonarLint LSP driver implementation, licensing and safety, and how VibeTrace achieves parity with Sonar rules via native Tree-sitter and Call Graph intelligence.

---

## 1. Architectural Families: Native vs. Sonar Sidecar

VibeTrace supports a tiered architecture for code quality:

| Family | Sub-Engines | Execution Model | Auto-Fix Support | Dependencies |
| :--- | :--- | :--- | :--- | :--- |
| **Family 1 (Primary Native)** | Native AST + Tree-Sitter + Ruff, Biome, Clippy, GolangCI-Lint, PHPStan, RuboCop, ShellCheck, SQLFluff | Subprocess CLI / In-Process Tree-Sitter & Python AST | ✅ Native (`--fix` in Ruff, Biome, Pint, SQLFluff) | Zero to minimal ecosystem tooling |
| **Family 2 (Optional Sidecar)** | SonarLint Headless Language Server (`sonarlint-ls.jar`) | Long-lived JSON-RPC stdio daemon on Java 21 | ❌ Read-only diagnostics | Java 21+ JRE & `sonarlint-ls.jar` |

### Why Family 1 Is the Default
- **Instant Speed**: Tree-sitter and native AST inspect Python, TypeScript, Go, Rust, PHP, and Ruby in $<15\text{ms}$ with zero cold-start delay.
- **SonarSource RSPEC Parity**: Native detection of Cognitive Complexity (`S3776`), Deep Nesting (`S134`), String Duplication (`S1192`), POSIX file permissions (`S2612`), and Async without Await (`S7503`).
- **Auto-Fixing**: Native ecosystem linters (Ruff, Biome, Laravel Pint, SQLFluff) can fix formatting, imports, and syntax smells automatically in-place.
- **Zero Binary Burden**: Developers don't need a 300MB Java runtime or Sonar jars to detect cognitive complexity, parameter bloat, or dead methods.

---

## 2. SonarSource Licensing & Enterprise Compliance

A key question when integrating Sonar technology is open-source compliance:

1. **Sonar Analyzer Open-Source Status**:
   - The individual Sonar language analyzers (`sonar-php`, `sonar-python`, `sonar-javascript`, `sonar-java`) are open-sourced by SonarSource under the **GNU Lesser General Public License v3.0 (LGPL v3.0)**.
   - Code repositories: [SonarSource/sonar-php](https://github.com/SonarSource/sonar-php), [SonarSource/sonarlint-core](https://github.com/SonarSource/sonarlint-core).
2. **Process Boundary Isolation**:
   - When VibeTrace spawns `java -jar sonarlint-ls.jar` as an independent subprocess communicating over stdin/stdout via standard JSON-RPC 2.0, it interacts across a process boundary.
   - Under LGPL v3.0, running an external program or communicating via RPC does **not** create a derivative work. VibeTrace's own permissive license remains completely unencumbered.
3. **Enterprise Portability**:
   - Organizations that already run SonarQube or SonarCloud can point VibeTrace to their local Sonar LSP server to synchronize corporate quality profile rules directly.

---

## 3. Headless SonarLint LSP Protocol

SonarLint Language Server (`sonarlint-ls.jar`) implements the Language Server Protocol (LSP) with custom SonarSource extensions:

```
VibeTrace Python Driver                      SonarLint LSP (Java 21)
         │                                              │
         ├─── initialize (ClientCapabilities) ─────────►│
         │◄── initializeResult ─────────────────────────┤
         ├─── initialized notification ────────────────►│
         │                                              │
         │◄── sonarlint/listFilesInFolder request ──────┤
         ├─── return FoundFileDto[] ───────────────────►│
         │                                              │
         ├─── textDocument/didOpen ────────────────────►│
         │◄── textDocument/publishDiagnostics ──────────┤ (S3776, S1144, S107...)
         │                                              │
```

### Key Protocol Handshakes:
1. **Telemetry Storage**: `initialize` params must include `initializationOptions: {"telemetryStorage": "<temp_dir>"}`.
2. **FoundFileDto Record Structure**: When the server asks `sonarlint/listFilesInFolder`, each item must be formatted as:
   ```json
   {
     "fileName": "sample_quality.php",
     "filePath": "tests/fixtures/sample_quality.php",
     "content": "<base64 or utf8 source>"
   }
   ```
3. **Workspace File Inclusion**: Sonar automatically ignores directories matching `.gitignore` (such as `/scratch/`). Test fixtures must be located in recognized source paths like `tests/fixtures/`.

A standalone verified test harness is located at `scratch/sonarlint_php_driver.py`.

---

## 4. VibeTrace Native Tree-Sitter & Call Graph Parity

To deliver Sonar-grade accuracy without requiring Java or Sonar jars, VibeTrace incorporates native AST engines:

### 1. Universal Tree-Sitter Cognitive Complexity (`RSPEC-3776`)
- Implemented in `src/vibetrace/quality/treesitter_complexity.py`.
- Adheres to the SonarSource Cognitive Complexity Whitepaper:
  - Base increment (+1) for branching: `if`, `ternary`, `switch`, `for`, `foreach`, `while`, `catch`, `&&`, `||`.
  - Nesting penalty (+1 per nesting level) for nested structural blocks.
  - Zero nesting penalty for `else` / `elseif` and flattened ternary conditions.
- **Verification**: In head-to-head benchmarking on `tests/fixtures/sample_quality.php`, Sonar scored `calculateScore()` at **33**. VibeTrace native Tree-Sitter scored **33**.

### 2. Dead / Unused Private Methods (`RSPEC-1144`)
- Implemented in `src/vibetrace/indexer/parsers/generic_parser.py` and `src/vibetrace/indexer/db_queries.py`.
- Tree-sitter extracts the `visibility_modifier` (`private`, `protected`, `public`) across languages and records it on AST nodes.
- Call Graph query `get_uncalled_private_functions()` finds all nodes where `visibility = 'private'` and in-degree calls $= 0$.
- Flags `php:S1144` or `python:S1144` with exact line number and symbol name.

### 3. Syntax Smells (`S107`, `S134`, `S1066`, `S1192`, `S3358`, `S3626`, `S7503`)
- `*:S107`: Method parameter count $> 7$.
- `*:S134`: Control flow statement nested too deeply ($> 3$ levels) — polyglot Tree-sitter AST detection across Python, TS/JS, Go, Rust, Java, C/C++, PHP, Ruby.
- `*:S1066`: Merging consecutive `if` statements without `else` using logical `and`/`&&`.
- `*:S1192`: String literals ($\ge 8$ chars) duplicated $\ge 3$ times — polyglot Tree-sitter and Python AST detection.
- `*:S3358`: Disallowing nested ternary expressions.
- `*:S3626`: Redundant `return` jumps at the end of void functions.
- `python:S7503`: Converting `async def` without `await` to synchronous `def`.

### 4. Automated Safe Fixing (`vibetrace audit quality --fix`)
- Synthesizes surgical AST codemods (`S7503` async def to def, `S1066` nested if merging).
- Automatically triggers ecosystem linters (`ruff check --fix`, `ruff format`, `biome check --write`, `sqlfluff --fix`).

### 5. SARIF 2.1.0 & Map Room Web UI Integration
- **SARIF Export**: `vibetrace audit quality --sarif` produces OASIS SARIF 2.1.0 JSON reports compatible with GitHub Code Scanning, SonarQube importers, and IDE SARIF extensions.
- **Map Room Quality Card**: Real-time visualization in the Map Room drawer with Cognitive Complexity heatmaps (green $\le 15$, yellow $16-25$, red $> 25$), Executive Findings Matrix, and 1-click in-browser baseline approvals (`GET /api/quality/audit`, `POST /api/quality/approve`).

---

## 5. Sub-Engine Provisioning CLI (`vibetrace tools`) & Optional `--sonar` Mode

Developers can verify and provision quality sub-engines on demand:

```bash
# Check status of all ecosystem engines (Ruff, Biome, Clippy, PHPStan, SonarLint)
vibetrace tools status
vibetrace tools doctor
vibetrace tools status --json

# Provision language runner on demand
vibetrace tools install php        # Downloads phpstan.phar into ~/.vibetrace/bin
vibetrace tools install python     # Guides/executes uv tool install ruff pyright
vibetrace tools install typescript # Guides/executes bun add -d @biomejs/biome
vibetrace tools install sonar      # Provisions sonarlint-ls.jar + JRE + analyzers into ~/.vibetrace/tools/sonar/
```

### Running the Optional Tier-2 SonarLint Engine (`--sonar`)
While Family 1 (Native AST + fast ecosystem linters) runs by default in `<100ms`, you can invoke the headless `SonarLintAdapter` (`src/vibetrace/quality/adapters/sonar_lint.py`) on demand:

```bash
# Run quality audit combining Native AST + SonarLint Headless Java Engine
vibetrace audit quality --sonar
vibetrace audit quality --file tests/fixtures/sample_quality.php --include-tests --sonar

# Run security audit including SonarLint vulnerability & ReDoS analyzers
vibetrace audit security --sonar

# Run unified 1-call review with SonarLint enabled
vibetrace review --sonar
```

- **H2 Database Lock Isolation**: `SonarLintAdapter` passes `-Duser.home=~/.vibetrace/tools/sonar/work` to the Java subprocess so its H2 database (`sq-ide.mv.db`) never collides with an active VS Code or Antigravity IDE instance.
- **Canonical RSPEC Deduplication**: Findings reported by both VibeTrace's native AST engine and `sonarlint-ls.jar` on the same `(file_path, start_line, rspec_id)` are automatically deduplicated.

---

## 6. Offline Quality Catalog & Bundling Architecture

VibeTrace ships with a comprehensive offline quality rules dictionary (`quality_rules_catalog.json` and `.gz`) containing metadata, clean-code tags, and CWE/OWASP mappings for over 4,000 RSPEC rules across 24 languages.

### Offline-First Philosophy
- **Zero Runtime Downloads**: End-user installations never fetch remote catalogs from GitHub or Sonar servers during auditing. The catalog is pre-bundled in the wheel and loaded directly from disk via `BUNDLED_CATALOG_PATH`.
- **Air-Gapped & CI Ready**: Eliminates network latency, corporate proxy issues, GitHub rate limits, and supply-chain hash drift in secure CI environments.

### Maintenance & On-Demand Catalog Compilation
Weekly CI sync jobs have been retired in favor of static, deterministic bundle releases. The rule extraction script remains available at `scripts/export_rules_manifest.py` to recompile the catalog whenever SonarSource releases a new major rule taxonomy:

```bash
# 1. Spin up ephemeral SonarQube container
docker run -d --rm -p 9000:9000 --name sonarqube-export sonarqube:community

# 2. Compile full rule manifest and gzip asset
uv run python scripts/export_rules_manifest.py --url http://localhost:9000 --gzip

# 3. Stop container
docker stop sonarqube-export
```
The resulting catalog files (`src/vibetrace/resources/quality_rules_catalog.json*`) are committed directly to version control and included in subsequent package builds.

