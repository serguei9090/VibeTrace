# Catálogo y Análisis de Excepciones Aprobadas en Baseline

**Archivo exportado en JSON completo:** [`docs/FullTest/approved-exceptions.json`](file:///docs/FullTest/approved-exceptions.json)  
**Total de Excepciones Aprobadas en Baseline:** 1,569 entradas registradas en [`.vibetrace/quality.toml`](file:///.vibetrace/quality.toml)  
**Total de Hallazgos Activos en Código Fuente:** 1,256 hallazgos

---

## 🔍 1. ¿Por qué son tantas y qué representan?

El 80% de todas las excepciones aprobadas pertenecen a **solo 3 categorías inherentes a la naturaleza de un analizador de código fuente y visualizador gráfico**:

### 1.1 Anidamiento de Bloques en Parsers (`S134` / Deep Nesting) — **625 casos (40%)**
- **Regla Sonar:** No anidar sentencias de control (`if`, `for`, `while`, `match`) a más de 3 o 4 niveles de profundidad.
- **¿Por qué son tantas?:**
  - VibeTrace parsea árboles de sintaxis abstracta (AST) de **12 lenguajes** (Python, TypeScript, Go, Rust, Ruby, PHP, Java, C#, etc.).
  - En un parser Tree-sitter, la inspección de un nodo requiere descender por la jerarquía del lenguaje:
    `if is_call -> if has_receiver -> if receiver_is_ident -> for arg in args -> if arg.kind == ...`
  - Archivos más impactados:
    - [`src/vibetrace/indexer/parsers/go_parser.py`](file:///src/vibetrace/indexer/parsers/go_parser.py) (81 casos)
    - [`src/web/src/features/map-room/utils/graphTraversal.ts`](file:///src/web/src/features/map-room/utils/graphTraversal.ts) (62 casos en algoritmos de recorrido de grafos)
    - [`src/vibetrace/indexer/parsers/js_parser.py`](file:///src/vibetrace/indexer/parsers/js_parser.py) (59 casos)

### 1.2 Cadenas de Texto Duplicadas (`S1192` / Duplicate String Literals) — **416 casos (27%)**
- **Regla Sonar:** Extraer cualquier cadena repetida más de 3 veces a una constante global (`UPPER_CASE_NAME = "valor"`).
- **¿Por qué son tantas?:**
  - Cadenas repetidas en la definición de la interfaz de línea de comandos (CLI), como `"store_true"`, `"--path"`, `"help"`, `"status"`.
  - Nombres de tablas y columnas relacionales en SQLite (`"nodes"`, `"edges"`, `"file_path"`, `"qualified_name"`).
  - Clases CSS y nombres de nodos React en el frontend (`"vibe-node"`, `"primary"`, `"table"`).
  - *Refactorizar 400 cadenas comunes a constantes globales aumentaría la complejidad cognitiva y el acoplamiento sin añadir valor real.*

### 1.3 Complejidad Cognitiva en Builders y Motores (`S3776` / Cognitive Complexity) — **233 casos (15%)**
- **Regla Sonar:** El puntaje de complejidad cognitiva no debe superar 15 por función.
- **¿Por qué son tantas?:**
  - `build_parser()` en [`cli.py`](file:///src/vibetrace/cli.py): Declara más de 25 subcomandos y 80 banderas de línea de comandos. Aunque su flujo es meramente declarativo, Sonar suma 1 punto por cada `add_argument()`.
  - Algoritmos de Layout de Grafos (ELK / WebGPU en React Flow) y extractores políglotas.

---

## 📊 2. Tabla Resumen de Reglas en Baseline

| Regla | Severidad | Descripción del Análisis | Casos | Archivos Principales |
|:---|:---:|:---|:---:|:---|
| **`python:S134` / `typescript:S134`** | `MEDIUM` | Estructuras `if/for` con anidamiento > 3 niveles | **625** | `go_parser.py`, `graphTraversal.ts`, `js_parser.py` |
| **`python:S1192` / `typescript:S1192`** | `LOW` | Cadenas de texto repetidas (`"store_true"`, `"calls"`, etc.) | **416** | `cli.py`, `DashboardTriageTables.tsx`, `tools_manager.py` |
| **`python:S3776` / `typescript:S3776`** | `HIGH` | Complejidad cognitiva > 15 en funciones grandes | **233** | `python_parser.py`, `graphTraversal.ts`, `webgpuRenderer.ts` |
| **`python:S1066`** | `MEDIUM` | `if` anidados que podrían colapsarse con `and` | **81** | `js_parser.py`, `go_parser.py`, `rust_parser.py` |
| **`python:S8415`** | `MEDIUM` | `HTTPException(400/404)` no documentada en OpenAPI `responses` | **30** | `server_api.py`, `server_api_engineering.py` |
| **`python:S5843`** | `MEDIUM` | Expresiones regulares con complejidad > 20 | **27** | `route_extractor.py`, `git_secret_scanner.py` |
| **`python:S7503`** | `MEDIUM` | Herramientas MCP síncronas que no usan `async def` | **25** | `src/vibetrace/mcp/server.py` |
| **`python:S107`** | `LOW` | Métodos con más de 7 parámetros | **22** | `service.py`, `base.py`, `route_extractor.py` |
| **`python:S9409`** | `MEDIUM` | `list.append()` consecutivos en vez de `list.extend()` | **12** | `baseline.py`, `formatters.py`, `vibe_bundle.py` |
| **Otras reglas** | Varios | Métodos privados de contratos de interfaz, Lambdas duplicadas | **98** | Adaptadores de linters y plugins |

---

## 🛡️ 3. ¿Por qué es seguro mantener este Baseline?

1. **Inmutabilidad por SHA-256:**
   Cada entrada en el baseline guarda el `code_hash` exacto del bloque de código.
2. **Cero Tolerancia a Regresiones:**
   Si un desarrollador o agente introduce **un nuevo `if` anidado, una nueva función compleja o una vulnerabilidad**, esa función **no coincidirá con el hash del baseline** y el CI fallará inmediatamente.
3. **Productividad Real:**
   Evita que el equipo gaste tiempo reescribiendo código de parsing que ya funciona de forma determinista para complacer una métrica cosmética de linter.
