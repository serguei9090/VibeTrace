"""Tests for Enterprise Polyglot Expansion (Java Spring Boot, C# ASP.NET Core, C++)."""

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all
from vibetrace.services.graph_service import (
    get_code_slice,
    trace_execution_path,
)


def test_java_spring_boot_route_extraction_and_trace(tmp_path: Path):
    """Verify Spring Boot route extraction and BFS trace."""
    pkg = tmp_path / "com" / "example" / "shop"
    pkg.mkdir(parents=True)

    (pkg / "OrderRepository.java").write_text(
        """package com.example.shop;

public class OrderRepository {
    public Object find(String id) {
        return null;
    }
}
""",
        encoding="utf-8",
    )

    (pkg / "OrderService.java").write_text(
        """package com.example.shop;

public class OrderService {
    private OrderRepository repository;

    public Object findById(String id) {
        return repository.find(id);
    }
}
""",
        encoding="utf-8",
    )

    (pkg / "OrderController.java").write_text(
        """package com.example.shop;

import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PathVariable;

@RestController
@RequestMapping("/api/v1/orders")
public class OrderController {
    private OrderService orderService;

    @GetMapping("/{id}")
    public Object getOrder(@PathVariable String id) {
        return orderService.findById(id);
    }

    @PostMapping
    public Object createOrder() {
        return null;
    }
}
""",
        encoding="utf-8",
    )

    db = IndexerDB(str(tmp_path / "java_test.db"))
    reindex_all(db, str(tmp_path), verbose=False)

    # 1. Verify synthetic Endpoint nodes were created
    get_ep = db.find_nodes_by_name("GET /api/v1/orders/{id}")
    assert len(get_ep) == 1
    assert get_ep[0]["kind"] == "endpoint"

    post_ep = db.find_nodes_by_name("POST /api/v1/orders")
    assert len(post_ep) == 1
    assert post_ep[0]["kind"] == "endpoint"

    # 2. Verify routing edge to OrderController.getOrder
    neighbors = db.get_neighbors(get_ep[0]["id"], direction="down")
    routes_targets = [n for n in neighbors if n["relation_type"] == "routes"]
    assert len(routes_targets) == 1
    assert "getOrder" in routes_targets[0]["name"]

    # 3. Verify execution trace from HTTP endpoint to OrderRepository.find
    trace_res = trace_execution_path(db, "GET /api/v1/orders/{id}", "OrderRepository.find")
    assert trace_res.found is True
    assert len(trace_res.hops) >= 3
    node_names = [h.from_node.name for h in trace_res.hops] + [trace_res.hops[-1].to_node.name]
    assert "GET /api/v1/orders/{id}" in node_names
    assert "getOrder" in node_names
    assert "findById" in node_names
    assert "find" in node_names


def test_csharp_aspnet_route_extraction_and_trace(tmp_path: Path):
    """Verify C# ASP.NET Core route attributes ([ApiController], [Route], [HttpPost]) and trace."""
    pkg = tmp_path / "Billing"
    pkg.mkdir()

    (pkg / "BillingService.cs").write_text(
        """namespace Billing;

public class BillingService {
    public bool ProcessPayment(string token) {
        return true;
    }
}
""",
        encoding="utf-8",
    )

    (pkg / "BillingController.cs").write_text(
        """namespace Billing;

using Microsoft.AspNetCore.Mvc;

[ApiController]
[Route("api/[controller]")]
public class BillingController : ControllerBase {
    private BillingService _billingService;

    [HttpPost("charge")]
    public bool Charge(string token) {
        return _billingService.ProcessPayment(token);
    }

    [HttpGet("{id}")]
    public string GetStatus(string id) {
        return "paid";
    }
}
""",
        encoding="utf-8",
    )

    db = IndexerDB(str(tmp_path / "csharp_test.db"))
    reindex_all(db, str(tmp_path), verbose=False)

    # 1. Verify Endpoint nodes with replaced [controller] -> billing
    charge_ep = db.find_nodes_by_name("POST /api/billing/charge")
    assert len(charge_ep) == 1
    assert charge_ep[0]["kind"] == "endpoint"

    status_ep = db.find_nodes_by_name("GET /api/billing/{id}")
    assert len(status_ep) == 1
    assert status_ep[0]["kind"] == "endpoint"

    # 2. Verify routing edge to BillingController.Charge
    neighbors = db.get_neighbors(charge_ep[0]["id"], direction="down")
    routes_targets = [n for n in neighbors if n["relation_type"] == "routes"]
    assert len(routes_targets) == 1
    assert "Charge" in routes_targets[0]["name"]

    # 3. Verify trace from endpoint to BillingService.ProcessPayment
    trace_res = trace_execution_path(
        db, "POST /api/billing/charge", "BillingService.ProcessPayment"
    )
    assert trace_res.found is True
    assert len(trace_res.hops) >= 2
    node_names = [h.from_node.name for h in trace_res.hops] + [trace_res.hops[-1].to_node.name]
    assert "POST /api/billing/charge" in node_names
    assert "Charge" in node_names
    assert "ProcessPayment" in node_names


def test_cpp_header_and_implementation_slicing(tmp_path: Path):
    """Verify C++ class declaration, implementation, and surgical slicing."""
    cpp_dir = tmp_path / "src"
    cpp_dir.mkdir()

    (cpp_dir / "math_engine.h").write_text(
        """#ifndef MATH_ENGINE_H
#define MATH_ENGINE_H

class MathEngine {
public:
    int computeFactorial(int n);
};

#endif
""",
        encoding="utf-8",
    )

    (cpp_dir / "math_engine.cpp").write_text(
        """#include "math_engine.h"

int MathEngine::computeFactorial(int n) {
    if (n <= 1) return 1;
    return n * computeFactorial(n - 1);
}
""",
        encoding="utf-8",
    )

    (cpp_dir / "main.cpp").write_text(
        """#include "math_engine.h"

int main() {
    MathEngine engine;
    return engine.computeFactorial(5);
}
""",
        encoding="utf-8",
    )

    db = IndexerDB(str(tmp_path / "cpp_test.db"))
    reindex_all(db, str(tmp_path), verbose=False)

    # Verify MathEngine class and computeFactorial function are indexed
    nodes = db.find_nodes_by_name("computeFactorial")
    assert len(nodes) >= 1

    # Verify surgical code slice extracts implementation lines
    slice_res = get_code_slice(db, tmp_path, "MathEngine.computeFactorial")
    assert slice_res.found is True
    assert "return n * computeFactorial" in slice_res.source_code
    assert slice_res.start_line is not None
    assert slice_res.start_line > 0
