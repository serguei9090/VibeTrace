# Refactor

## Overview

Data contracts for AST-based method extraction and code refactoring. Holds the candidate split-block descriptor produced by complexity analysis and the full refactoring plan blueprint consumed by the refactoring service when applying a helper-function extraction.

## Interface

- [`__init__.py`](./__init__.py) — package marker with docstring `AST refactoring and method extraction package.`; exports no symbols.
- [`models.py`](./models.py) — `CandidateSplitBlock` dataclass and `RefactorPlan` dataclass.

`CandidateSplitBlock` fields: `start_line`, `end_line`, `complexity_score`, `complexity_reduction`, `inputs`, `outputs`, `statement_types`, `summary`, plus `to_dict()`.

`RefactorPlan` fields: `target_function`, `file_path`, `start_line`, `end_line`, `helper_name`, `helper_code`, `call_replacement`, `original_snippet`, `refactored_full_source`, `can_auto_apply`, `rejection_reason` (optional), `diff`, `inputs`, `outputs`, plus `to_dict()`.

## Invariants

- `CandidateSplitBlock.to_dict()` returns a plain dictionary via `dataclasses.asdict`, so every field is JSON-serializable [src/vibetrace/refactor/models.py:CandidateSplitBlock].
- `RefactorPlan.to_dict()` returns a plain dictionary via `dataclasses.asdict` [src/vibetrace/refactor/models.py:RefactorPlan].
- `RefactorPlan.rejection_reason` defaults to `None`; `diff` defaults to the empty string; `inputs` and `outputs` default to empty lists via `field(default_factory=list)` [src/vibetrace/refactor/models.py:RefactorPlan].
- `RefactorPlan.can_auto_apply` is the gate that decides whether the plan may be written to disk; a plan with `can_auto_apply=False` carries the reason in `rejection_reason` [src/vibetrace/refactor/models.py:RefactorPlan].
- `CandidateSplitBlock.complexity_reduction` is expressed as the complexity drop obtained by extracting the block [src/vibetrace/refactor/models.py:CandidateSplitBlock].

## Side Effects

- None. Both classes are plain dataclasses with no I/O, no global state, and no external calls.

## Verification

- Run: `uv run pytest`
