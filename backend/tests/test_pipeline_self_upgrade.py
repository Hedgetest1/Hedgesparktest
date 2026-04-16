"""Tests for D5 — pipeline self-upgrade scan.

Contract:
  1. Missing pip-audit binary → empty scan, no crash.
  2. pip-audit output is parsed into normalized vuln records
     (both dependencies-schema and list schema).
  3. `_upsert_candidate_for_vuln` creates a TIER_2 BugFixCandidate
     with source_type='dep_upgrade' and a rich context payload.
  4. Dedup: a second call with the same (package, vuln_id) in the
     lookback window returns None (no duplicate row).
  5. `run_self_upgrade_scan` returns an accurate report dict.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

from app.models.bugfix_candidate import BugFixCandidate
from app.services import pipeline_self_upgrade as psu


def _fake_pip_audit_dependencies_schema():
    return {
        "dependencies": [
            {
                "name": "requests",
                "version": "2.28.0",
                "vulns": [
                    {
                        "id": "GHSA-j8r2-6x86-q33q",
                        "fix_versions": ["2.31.0"],
                        "description": "urllib3 proxy leak",
                        "aliases": ["CVE-2023-32681"],
                    }
                ],
            },
            {
                "name": "pyyaml",
                "version": "5.3",
                "vulns": [
                    {
                        "id": "GHSA-8q59-q68h-6hv4",
                        "fix_versions": ["5.4"],
                        "description": "YAML deserialization",
                    }
                ],
            },
            {
                "name": "clean-pkg",
                "version": "1.0.0",
                "vulns": [],
            },
        ]
    }


def _fake_pip_audit_list_schema():
    return [
        {
            "name": "jinja2",
            "version": "2.10",
            "vulns": [
                {
                    "id": "PYSEC-2020-66",
                    "fix_versions": ["2.11.3"],
                    "description": "template injection",
                }
            ],
        }
    ]


# ---------- Normalization ----------

def test_normalize_dependencies_schema():
    records = psu._normalize_pip_audit_output(_fake_pip_audit_dependencies_schema())
    assert len(records) == 2  # clean-pkg dropped
    ids = {r["vuln_id"] for r in records}
    assert "GHSA-j8r2-6x86-q33q" in ids
    assert "GHSA-8q59-q68h-6hv4" in ids
    first = next(r for r in records if r["package"] == "requests")
    assert first["current_version"] == "2.28.0"
    assert first["fix_versions"] == ["2.31.0"]


def test_normalize_list_schema():
    records = psu._normalize_pip_audit_output(_fake_pip_audit_list_schema())
    assert len(records) == 1
    assert records[0]["package"] == "jinja2"


def test_normalize_malformed_input_returns_empty():
    assert psu._normalize_pip_audit_output(None) == []
    assert psu._normalize_pip_audit_output("garbage") == []
    assert psu._normalize_pip_audit_output({"no_deps": True}) == []


# ---------- Discovery (binary missing path) ----------

def test_discover_returns_empty_when_binary_missing():
    with patch.object(psu, "_pip_audit_binary", return_value=None):
        assert psu._discover_vulnerabilities() == []


def test_discover_parses_subprocess_output():
    raw = json.dumps(_fake_pip_audit_dependencies_schema())

    class _Proc:
        stdout = raw
        stderr = ""

    with patch.object(psu, "_pip_audit_binary", return_value="/fake/bin/pip-audit"), \
            patch.object(psu.subprocess, "run", return_value=_Proc()):
        records = psu._discover_vulnerabilities()
    assert len(records) == 2


def test_discover_handles_json_parse_error():
    class _Proc:
        stdout = "not json {{"
        stderr = ""

    with patch.object(psu, "_pip_audit_binary", return_value="/fake"), \
            patch.object(psu.subprocess, "run", return_value=_Proc()):
        assert psu._discover_vulnerabilities() == []


# ---------- Candidate creation ----------

def _vuln(package: str, vuln_id: str) -> dict:
    return {
        "package": package,
        "current_version": "1.0.0",
        "vuln_id": vuln_id,
        "fix_versions": ["1.0.1"],
        "description": "test vuln",
        "aliases": [],
    }


def test_upsert_creates_tier2_candidate(db):
    v = _vuln(f"pkg_{uuid.uuid4().hex[:6]}", f"TESTVULN-{uuid.uuid4().hex[:8]}")
    cid = psu._upsert_candidate_for_vuln(db, v)
    assert cid is not None
    c = db.get(BugFixCandidate, cid)
    assert c.source_type == "dep_upgrade"
    assert c.patch_risk_tier == 2
    assert c.status == "open"
    assert c.affected_domain == "dependencies"
    ctx = json.loads(c.context_json)
    assert ctx["package"] == v["package"]
    assert ctx["vuln_id"] == v["vuln_id"]
    assert ctx["fix_versions"] == ["1.0.1"]
    assert ctx["scanner"] == "pip-audit"


def test_upsert_is_deduped(db):
    v = _vuln(f"dedup_{uuid.uuid4().hex[:6]}", f"DEDUP-{uuid.uuid4().hex[:8]}")
    first = psu._upsert_candidate_for_vuln(db, v)
    second = psu._upsert_candidate_for_vuln(db, v)
    assert first is not None
    assert second is None


def test_upsert_allows_different_vuln_in_same_package(db):
    pkg = f"multi_{uuid.uuid4().hex[:6]}"
    a = psu._upsert_candidate_for_vuln(db, _vuln(pkg, f"A-{uuid.uuid4().hex[:6]}"))
    b = psu._upsert_candidate_for_vuln(db, _vuln(pkg, f"B-{uuid.uuid4().hex[:6]}"))
    assert a is not None and b is not None and a != b


# ---------- run_self_upgrade_scan end-to-end ----------

def test_run_scan_reports_counts(db):
    fake_vulns = [
        _vuln(f"rpt_{uuid.uuid4().hex[:6]}", f"R1-{uuid.uuid4().hex[:6]}"),
        _vuln(f"rpt_{uuid.uuid4().hex[:6]}", f"R2-{uuid.uuid4().hex[:6]}"),
    ]
    with patch.object(psu, "_discover_vulnerabilities", return_value=fake_vulns):
        report = psu.run_self_upgrade_scan(db)
    assert report["vulnerabilities_found"] == 2
    assert report["candidates_created"] == 2
    assert len(report["created_ids"]) == 2


def test_run_scan_noop_when_no_vulns(db):
    with patch.object(psu, "_discover_vulnerabilities", return_value=[]):
        report = psu.run_self_upgrade_scan(db)
    assert report["vulnerabilities_found"] == 0
    assert report["candidates_created"] == 0


def test_run_scan_deduplicates_within_window(db):
    v = _vuln(f"window_{uuid.uuid4().hex[:6]}", f"WIN-{uuid.uuid4().hex[:8]}")
    with patch.object(psu, "_discover_vulnerabilities", return_value=[v]):
        first = psu.run_self_upgrade_scan(db)
        second = psu.run_self_upgrade_scan(db)
    assert first["candidates_created"] == 1
    assert second["candidates_created"] == 0
    assert second["candidates_skipped_dedup"] == 1
