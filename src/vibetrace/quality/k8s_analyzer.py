"""Kubernetes and YAML Maintainability and Quality Analyzer.

Evaluates Kubernetes manifests and YAML files for clean code and production hygiene:
- k8s:S101 / RSPEC-101: Container lacks CPU/memory resource requests or limits
- k8s:S102 / RSPEC-102: Workload container lacks readiness or liveness probe
- k8s:S103 / RSPEC-103: Container image uses ':latest' or unversioned tag
- yaml:S104 / RSPEC-104: Duplicate mapping keys in YAML document
"""

from __future__ import annotations

import hashlib
import re

from vibetrace.quality.models import (
    QualityCategory,
    QualityFinding,
    QualitySeverity,
)

_K8S_WORKLOAD_KINDS = frozenset(
    {"deployment", "statefulset", "daemonset", "pod", "job", "cronjob", "replicaset"}
)
_IMAGE_LINE_RE = re.compile(r"^\s*image\s*:\s*['\"]?([^\s'\"]+)['\"]?", re.IGNORECASE)
_CONTAINER_NAME_RE = re.compile(r"^\s*-\s*name\s*:\s*([^\s]+)", re.IGNORECASE)
_YAML_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_\-\.]+)\s*:")


class K8sQualityAnalyzer:
    """Audits Kubernetes manifests and YAML files for maintainability."""

    def __init__(self, file_path: str, source_code: str) -> None:
        self.file_path = file_path.replace("\\", "/")
        self.source_code = source_code
        self.source_lines = source_code.splitlines()
        self.findings: list[QualityFinding] = []

    def _make_id(self, rule_id: str, line_no: int, desc: str) -> str:
        raw = f"{self.file_path}:{line_no}:{desc}"
        return f"QUAL-{rule_id.split(':')[-1]}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

    def _hash_snippet(self, snippet: str) -> str:
        return hashlib.sha256(snippet.strip().encode("utf-8")).hexdigest()

    def analyze(self) -> list[QualityFinding]:
        self.findings.clear()
        self._check_duplicate_keys()
        if self._is_k8s_manifest():
            self._check_k8s_containers()
        return self.findings

    def _check_duplicate_keys(self) -> None:
        """yaml:S104: Flags duplicate keys at the same indentation level."""
        # Key: (indent_len, doc_index) -> dict of key_name -> line_no
        indent_keys: dict[tuple[int, int], dict[str, int]] = {}
        doc_idx = 0

        for line_no, line in enumerate(self.source_lines, 1):
            stripped = line.strip()
            if stripped == "---":
                doc_idx += 1
                indent_keys.clear()
                continue
            if not stripped or stripped.startswith("#"):
                continue

            m = _YAML_KEY_RE.match(line)
            if m:
                indent = len(m.group(1))
                key = m.group(2)
                ctx = (indent, doc_idx)

                # Reset deeper indentation scopes
                keys_to_del = [k for k in indent_keys if k[0] > indent and k[1] == doc_idx]
                for k in keys_to_del:
                    del indent_keys[k]

                if ctx not in indent_keys:
                    indent_keys[ctx] = {}

                if key in indent_keys[ctx]:
                    prev_line = indent_keys[ctx][key]
                    self.findings.append(
                        QualityFinding(
                            id=self._make_id("yaml:S104", line_no, f"dup_{key}"),
                            rule_id="yaml:S104",
                            rspec_id="RSPEC-104",
                            category=QualityCategory.MAINTAINABILITY,
                            severity=QualitySeverity.MEDIUM,
                            title=f"Duplicate YAML key '{key}' detected.",
                            message=(
                                f"Key '{key}' is defined multiple times at the same indentation "
                                f"level (previously at line {prev_line})."
                            ),
                            file_path=self.file_path,
                            start_line=line_no,
                            end_line=line_no,
                            symbol_name=None,
                            code_hash=self._hash_snippet(line),
                            non_compliant_snippet=line.strip(),
                            compliant_snippet=None,
                            remediation_guidance="Remove or rename duplicate mapping key.",
                        )
                    )
                else:
                    indent_keys[ctx][key] = line_no

    def _is_k8s_manifest(self) -> bool:
        has_api = False
        has_kind = False
        for line in self.source_lines[:40]:
            lower = line.strip().lower()
            if lower.startswith("apiversion:"):
                has_api = True
            elif lower.startswith("kind:"):
                kind_val = lower.split(":", 1)[1].strip()
                if kind_val in _K8S_WORKLOAD_KINDS:
                    has_kind = True
        return has_api and has_kind

    def _check_k8s_containers(self) -> None:
        """Audits containers for missing resources, probes, and :latest image tags."""
        in_containers = False
        container_indent = 0
        curr_container_name = ""
        curr_container_line = 0
        has_resources = False
        has_probe = False

        def _flush_container() -> None:
            if not curr_container_name:
                return
            if not has_resources:
                self.findings.append(
                    QualityFinding(
                        id=self._make_id("k8s:S101", curr_container_line, curr_container_name),
                        rule_id="k8s:S101",
                        rspec_id="RSPEC-101",
                        category=QualityCategory.RELIABILITY,
                        severity=QualitySeverity.MEDIUM,
                        title=f"Container '{curr_container_name}' lacks resource requests.",
                        message=(
                            f"Container '{curr_container_name}' does not specify CPU/memory "
                            "resources. Unconstrained containers cause node memory exhaustion."
                        ),
                        file_path=self.file_path,
                        start_line=curr_container_line,
                        end_line=curr_container_line,
                        symbol_name=curr_container_name,
                        code_hash=self._hash_snippet(f"container:{curr_container_name}"),
                        non_compliant_snippet=f"- name: {curr_container_name}",
                        compliant_snippet="resources:\n  requests:\n    cpu: 100m",
                        remediation_guidance="Specify 'resources.requests' and 'resources.limits'.",
                    )
                )
            if not has_probe:
                self.findings.append(
                    QualityFinding(
                        id=self._make_id("k8s:S102", curr_container_line, curr_container_name),
                        rule_id="k8s:S102",
                        rspec_id="RSPEC-102",
                        category=QualityCategory.MAINTAINABILITY,
                        severity=QualitySeverity.LOW,
                        title=f"Container '{curr_container_name}' lacks readiness probe.",
                        message=(
                            f"Container '{curr_container_name}' does not configure livenessProbe "
                            "or readinessProbe for health lifecycle monitoring."
                        ),
                        file_path=self.file_path,
                        start_line=curr_container_line,
                        end_line=curr_container_line,
                        symbol_name=curr_container_name,
                        code_hash=self._hash_snippet(f"probe:{curr_container_name}"),
                        non_compliant_snippet=f"- name: {curr_container_name}",
                        compliant_snippet="readinessProbe:\n  httpGet:\n    path: /health",
                        remediation_guidance="Add 'readinessProbe' or 'livenessProbe'.",
                    )
                )

        for line_no, line in enumerate(self.source_lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            lower = stripped.lower()
            if lower.startswith("containers:") or lower.startswith("initcontainers:"):
                in_containers = True
                container_indent = len(line) - len(line.lstrip())
                continue

            if in_containers:
                curr_indent = len(line) - len(line.lstrip())
                if curr_indent <= container_indent and not lower.startswith("-"):
                    in_containers = False
                    _flush_container()
                    curr_container_name = ""
                    continue

                c_match = _CONTAINER_NAME_RE.match(line)
                if c_match:
                    _flush_container()
                    curr_container_name = c_match.group(1)
                    curr_container_line = line_no
                    has_resources = False
                    has_probe = False
                    continue

                if lower.startswith("resources:"):
                    has_resources = True
                elif lower.startswith("readinessprobe:") or lower.startswith("livenessprobe:"):
                    has_probe = True

                img_match = _IMAGE_LINE_RE.match(line)
                if img_match:
                    img_ref = img_match.group(1)
                    if ":" not in img_ref or img_ref.endswith(":latest"):
                        self.findings.append(
                            QualityFinding(
                                id=self._make_id("k8s:S103", line_no, img_ref),
                                rule_id="k8s:S103",
                                rspec_id="RSPEC-103",
                                category=QualityCategory.RELIABILITY,
                                severity=QualitySeverity.MEDIUM,
                                title=f"Container image '{img_ref}' uses ':latest' tag.",
                                message=(
                                    f"Image '{img_ref}' uses ':latest' or lacks an immutable tag. "
                                    "Pin images to specific versions or SHA digests."
                                ),
                                file_path=self.file_path,
                                start_line=line_no,
                                end_line=line_no,
                                symbol_name=curr_container_name or None,
                                code_hash=self._hash_snippet(line),
                                non_compliant_snippet=line.strip(),
                                compliant_snippet=f"image: {img_ref.split(':')[0]}:v1.0.0",
                                remediation_guidance="Pin image version to explicit tag or digest.",
                            )
                        )

        _flush_container()


def analyze_k8s_quality(file_path: str, source_code: str) -> list[QualityFinding]:
    """Helper entry point for auditing Kubernetes and YAML files."""
    analyzer = K8sQualityAnalyzer(file_path=file_path, source_code=source_code)
    return analyzer.analyze()
