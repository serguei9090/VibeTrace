# Components

## Overview

Unified AI Quality Gate dashboard: executive KPI cards plus triage tables for untested symbols, architectural splits, security findings, and remediation checklist.

## Interface

- [`DashboardView`](DashboardView.tsx) — props `{ apiUrl: string, rawNodes: RawGraphNode[], onSwitchToMapAndFocus: (nodeId: string) => void }`; scope state `"changed" | "full"` (default `"changed"`); `checkDeps` toggle for OSV lockfile CVEs; module-level `reviewCache` keyed `${scope}:${includeDeps}`.
- [`DashboardTriageTables`](DashboardTriageTables.tsx) — props `{ report: UnifiedReviewReportUI, allSplits: SplitItemUI[], allSecurityFindings: SecurityFindingUI[], onRefresh: () => void, onApproveSplit: (split, status: "approved" | "risk") => Promise<void>, onApproveSecurity: (finding, tag: string) => Promise<void>, onInspectSymbolOnMap: (symbol: string, filePath?: string) => void }`.
- [`SplitItemUI`](DashboardTriageTables.tsx) — exported split row shape: `node_id`, `symbol`, `qualified_name`, `file_path`, `start_line`, `end_line`, `code_hash`, `branches_count`, `branches?`, `status`, `rationale?`.

## Invariants

- Coverage verdict compares `effective_coverage_pct` (default 100) against `threshold_pct` (default 80) [src/web/src/features/dashboard/components/DashboardView.tsx:DashboardView].
- Security issue count = `unapproved_security_count + modified_security_count`; split issue count = `new_splits_count + modified_splits_count` [src/web/src/features/dashboard/components/DashboardView.tsx:DashboardView].
- `allSplits` falls back to `report.all_splits || report.actionable_splits || []`; `allSecurityFindings` falls back to `report.all_security_findings || report.security_findings || []` [src/web/src/features/dashboard/components/DashboardView.tsx:DashboardView].
- Triage tabs are the internal union `"security" | "splits" | "coverage" | "checklist"`; default tab `"coverage"` [src/web/src/features/dashboard/components/DashboardTriageTables.tsx:DashboardTriageTables].
- Untested symbols list capped at 120 rendered rows; `actionable` filters hide entries with `status === "APPROVED"` [src/web/src/features/dashboard/components/DashboardTriageTables.tsx:DashboardTriageTables].
- `handleInspectOnMap` resolves a node by name/qualified_name with optional `file_path` match before calling `onSwitchToMapAndFocus` [src/web/src/features/dashboard/components/DashboardView.tsx:DashboardView].

## Side Effects

- Approval requests clear `reviewCache` and force a refetch of the current scope [src/web/src/features/dashboard/components/DashboardView.tsx:DashboardView].
- `DashboardView`: GET `/api/review` (params `scope`, `check_dependencies`), POST `/api/splits/approve`, POST `/api/security/approve`; mutates module-level `reviewCache` [src/web/src/features/dashboard/components/DashboardView.tsx:DashboardView].
- `DashboardTriageTables`: invokes `onApproveSplit`, `onApproveSecurity`, `onInspectSymbolOnMap`, `onRefresh` callbacks supplied by `DashboardView` [src/web/src/features/dashboard/components/DashboardTriageTables.tsx:DashboardTriageTables].

## Verification

- Run: `cd src/web && bun run build`
