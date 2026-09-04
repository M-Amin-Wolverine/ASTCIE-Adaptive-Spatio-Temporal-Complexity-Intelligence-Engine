#!/usr/bin/env python3
"""Basic smoke tests for ASTCIE V8.1 / V9 engines."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO = Path(__file__).parent / "synthetic_test.mp4"
V8 = ROOT / "v8" / "complexity7.py"
V9 = ROOT / "v9" / "complexity8.py"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return r


def test_v8_runs():
    assert VIDEO.exists(), "synthetic video missing"
    r = run([sys.executable, str(V8), str(VIDEO), "--width", "320"])
    assert r.returncode == 0, r.stderr
    result = ROOT / "results" / "synthetic_test" / "result.json"
    assert result.exists()
    data = json.loads(result.read_text())
    assert "v8_fusion_score" in data
    assert "bit_demand_index" in data
    assert 0.0 <= data["v8_fusion_score"] <= 1.0
    assert 0.0 <= data["bit_demand_index"] <= 1.0
    print("PASS: V8.1 smoke test")


def test_v9_runs():
    assert VIDEO.exists()
    r = run([sys.executable, str(V9), str(VIDEO), "--width", "320"])
    assert r.returncode == 0, r.stderr
    result = ROOT / "results_v9" / "synthetic_test" / "result_v9.json"
    assert result.exists()
    data = json.loads(result.read_text())
    assert "v9_fusion_score" in data
    assert "bit_demand_index" in data
    assert 0.0 <= data["v9_fusion_score"] <= 1.0
    print("PASS: V9 smoke test")


if __name__ == "__main__":
    test_v8_runs()
    test_v9_runs()
    print("ALL TESTS PASSED")
