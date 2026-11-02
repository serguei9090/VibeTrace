"""Tests for Kubernetes and YAML quality and maintainability analyzer."""

from __future__ import annotations

from vibetrace.quality.k8s_analyzer import analyze_k8s_quality


def test_k8s_quality_analyzer_missing_resources_and_probes():
    manifest = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: payment-service
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: app
          image: myrepo/payment:v1.2.0
"""
    findings = analyze_k8s_quality("deploy/payment.yaml", manifest)
    rule_ids = [f.rule_id for f in findings]
    assert "k8s:S101" in rule_ids, "Should detect missing resource requests/limits"
    assert "k8s:S102" in rule_ids, "Should detect missing readiness/liveness probes"


def test_k8s_quality_analyzer_latest_image_tag():
    manifest = """apiVersion: v1
kind: Pod
metadata:
  name: worker
spec:
  containers:
    - name: worker
      image: redis:latest
      resources:
        requests:
          cpu: 100m
      readinessProbe:
        exec:
          command: ["redis-cli", "ping"]
"""
    findings = analyze_k8s_quality("deploy/worker.yaml", manifest)
    rule_ids = [f.rule_id for f in findings]
    assert "k8s:S103" in rule_ids, "Should detect :latest image tag"
    assert "k8s:S101" not in rule_ids, "Has resources"
    assert "k8s:S102" not in rule_ids, "Has probe"


def test_yaml_duplicate_key_detection():
    yaml_doc = """server:
  host: 0.0.0.0
  port: 8080
  port: 9090
"""
    findings = analyze_k8s_quality("config.yaml", yaml_doc)
    rule_ids = [f.rule_id for f in findings]
    assert "yaml:S104" in rule_ids, "Should detect duplicate YAML key"
    dup = next(f for f in findings if f.rule_id == "yaml:S104")
    assert "port" in dup.message
