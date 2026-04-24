"""Test audit_scheduled_jobs_map returns line numbers with drift output
(LOW-01 sibling, closed same turn).

The extractors return `{name: line_number}` so drift messages can emit
`app/workers/agent_worker.py:NN` and `docs/reality_scheduled_jobs.md:NN`
for direct operator navigation.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_scheduled_jobs_map.py")


def _load_module():
    name = "audit_scheduled_jobs_map_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_extract_documented_fns_returns_line_numbers():
    mod = _load_module()
    md = (
        "# Top\n"
        "\n"
        "## Internal sub-tasks inside agent_worker.py\n"
        "\n"
        "| Function | Purpose |\n"
        "|---|---|\n"
        "| `_run_alpha` | alpha |\n"
        "| `_run_beta`  | beta  |\n"
        "\n"
        "## Next section\n"
    )
    out = mod._extract_documented_fns(md)
    assert out == {"_run_alpha": 7, "_run_beta": 8}


def test_extract_defined_fns_returns_line_numbers(tmp_path):
    mod = _load_module()
    source = (
        "import x\n"
        "\n"
        "def _helper():\n"
        "    return 1\n"
        "\n"
        "def _run_alpha():\n"
        "    return 2\n"
        "\n"
        "async def _run_beta():\n"
        "    return 3\n"
    )
    py = tmp_path / "w.py"
    py.write_text(source)
    out = mod._extract_defined_fns(py)
    assert out == {"_run_alpha": 6, "_run_beta": 9}


def test_live_audit_clean_on_current_tree():
    """Sanity check against the real repo so the refactor can't silently
    stop detecting real drift."""
    mod = _load_module()
    rc = mod.main([])
    assert rc == 0
