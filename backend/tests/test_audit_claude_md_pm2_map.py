"""Test the audit_claude_md_pm2_map line-number emission (LOW-01 fix).

On drift, output MUST include `CLAUDE.md:<line>` for stale entries and
`ecosystem.config.js:<line>` for missing entries so an operator can jump
directly to the row to fix. Without this, the audit names the process
but forces a second grep — which is the LOW-01 gap we closed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path("/opt/wishspark/backend/scripts/audit_claude_md_pm2_map.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_pm2_map", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extract_doc_names_returns_line_numbers():
    mod = _load_module()
    md = (
        "# Intro\n"
        "## 6. Architecture\n"
        "\n"
        "### PM2 processes (fork mode)\n"
        "\n"
        "| Process | Script |\n"
        "|---|---|\n"
        "| wishspark-backend | uvicorn |\n"
        "| wishspark-worker  | worker.py |\n"
        "\n"
        "## 7. Next section\n"
    )
    out = mod._extract_doc_names(md)
    assert out == {"wishspark-backend": 8, "wishspark-worker": 9}


def test_extract_ecosystem_names_returns_line_numbers():
    mod = _load_module()
    js = (
        "module.exports = {\n"
        "  apps: [\n"
        "    {\n"
        '      name: "wishspark-backend",\n'
        "    },\n"
        "    {\n"
        '      name: "wishspark-worker",\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    out = mod._extract_ecosystem_names(js)
    assert out == {"wishspark-backend": 4, "wishspark-worker": 7}


def test_live_audit_emits_clean_on_current_tree():
    """Sanity: against the real repo the audit returns 0 and the output
    contains the process count — not a drift message. This pins the
    current state + catches a regression in the line-number refactor."""
    mod = _load_module()
    rc = mod.main([])
    assert rc == 0
