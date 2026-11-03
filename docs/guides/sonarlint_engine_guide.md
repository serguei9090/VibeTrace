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

### 3. Syntax Smells (`S107`, `S1066`, `S3358`, `S3626`)
- `php:S107`: Method parameter count $> 7$.
- `php:S1066`: Merging consecutive `if` statements without `else`.
- `php:S3358`: Disallowing nested ternary expressions.
- `php:S3626`: Redundant `return` jumps at the end of void functions.

---

## 5. Sub-Engine Provisioning CLI (`vibetrace tools`)

Developers can verify and provision quality sub-engines on demand:

```bash
# Check status of all ecosystem engines (Ruff, Biome, Clippy, PHPStan)
vibetrace tools status
vibetrace tools doctor
vibetrace tools status --json

# Provision language runner on demand
vibetrace tools install php       # Downloads phpstan.phar into ~/.vibetrace/bin
vibetrace tools install python    # Guides/executes uv tool install ruff pyright
vibetrace tools install typescript # Guides/executes bun add -d @biomejs/biome
```
