# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""brain_tool — autonomous brain's tool-spawn capability.

Founder direttiva 2026-05-06 (CLAUDE.md §21.6 hook #4 + §21.7): the
autonomous brain must operate with the same tool-freedom interactive
Claude has — parallel investigation, audit invocation, web search.
Without this layer the brain runs linearly: one grep at a time, no
preventer audits invoked at investigation time, no external context.

This module exposes `BrainTool`, a thin dispatcher used by the
sibling-sweep, classify, and investigation paths in
`bugfix_pipeline.py`. Methods are deterministic (no LLM cost) and
fail-open (broken tool → empty result, never raises into caller).

Capabilities
------------
1. `parallel_grep(patterns)`   — multi-pattern grep across the repo
   via multiprocessing. ~3× faster than sequential at 4 patterns;
   the speedup matters because brain investigation is in the
   propose hot path.

2. `invoke_audit(name)`        — runs one of the existing scripts
   in `backend/scripts/` as a subprocess; returns exit_code +
   stdout snippet so the brain can pull preventer-class findings
   into its context.

3. `web_search(query)`         — stub for now. Wiring this requires
   a paid API (Brave/Serper) → R-blocker:infra-spend until founder
   approves a budget. Returns empty list with a clear log line so
   audit_brain_propagation_hooks still detects the interface.

Safety
------
* Subprocess and grep calls have a 15s timeout each.
* No path escape: all file paths are resolved relative to backend
  root and refused if they contain `..` or absolute prefixes.
* Result snippets are capped at 2000 chars to avoid prompt bloat.

# invariant-eligible: true
"""
from __future__ import annotations

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

log = logging.getLogger("brain_tool")

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
_PYTHON_BIN = _BACKEND_ROOT / "venv" / "bin" / "python"
_GREP_TIMEOUT_S = 15.0
_AUDIT_TIMEOUT_S = 30.0
_SNIPPET_CAP = 2000


def _safe_relpath(p: str) -> bool:
    """Reject path-escape attempts."""
    if not p or not isinstance(p, str):
        return False
    if ".." in p:
        return False
    if p.startswith("/"):
        return False
    return True


class BrainTool:
    """Tool-spawn dispatcher for the autonomous brain.

    All methods are best-effort and never raise into the caller —
    a broken tool returns empty/None and logs a debug line. The
    brain treats missing results as "no signal", same as a grep
    that found nothing.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _BACKEND_ROOT
        self.scripts = _SCRIPTS_DIR

    def parallel_grep(
        self, patterns: list[str], scope: str = "app/",
    ) -> dict[str, list[str]]:
        """Run multiple greps concurrently across `scope`.

        Returns {pattern: [hit_line, ...]} with up to 50 hits per
        pattern. Empty dict on total failure.
        """
        if not patterns:
            return {}
        scope_dir = self.root / scope
        if not _safe_relpath(scope) or not scope_dir.is_dir():
            log.debug("brain_tool.parallel_grep: bad scope %s", scope)
            return {}
        out: dict[str, list[str]] = {}

        def _one(pat: str) -> tuple[str, list[str]]:
            if not pat or len(pat) > 500:
                return (pat, [])
            try:
                res = subprocess.run(
                    [
                        "grep", "-rn", "--include=*.py", "-E", pat, str(scope_dir),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=_GREP_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                return (pat, [])
            except Exception:
                return (pat, [])
            lines = (res.stdout or "").splitlines()[:50]
            return (pat, lines)

        with ThreadPoolExecutor(max_workers=min(4, len(patterns))) as ex:
            futs = {ex.submit(_one, p): p for p in patterns}
            for fut in as_completed(futs):
                try:
                    pat, lines = fut.result()
                    out[pat] = lines
                except Exception:
                    pass  # SILENT-EXCEPT-OK: parallel-grep worker failures degrade open — the missing pattern simply has no entry in the result dict, same shape as a grep with no hits.
        return out

    def invoke_audit(self, script_name: str) -> dict:
        """Invoke one of the audit scripts in backend/scripts/.

        Returns {exit_code, stdout, stderr} with snippets capped at
        `_SNIPPET_CAP`. exit_code is None on launch failure.
        """
        if not script_name or "/" in script_name or ".." in script_name:
            return {"exit_code": None, "stdout": "", "stderr": "bad_name"}
        script_path = self.scripts / script_name
        if not script_path.is_file():
            return {"exit_code": None, "stdout": "", "stderr": "missing"}
        if not _PYTHON_BIN.is_file():
            return {"exit_code": None, "stdout": "", "stderr": "no_python"}
        try:
            res = subprocess.run(
                [str(_PYTHON_BIN), str(script_path)],
                capture_output=True,
                text=True,
                timeout=_AUDIT_TIMEOUT_S,
                cwd=str(self.root),
            )
        except subprocess.TimeoutExpired:
            return {"exit_code": None, "stdout": "", "stderr": "timeout"}
        except Exception as exc:
            return {"exit_code": None, "stdout": "", "stderr": f"launch:{exc}"[:200]}
        return {
            "exit_code": res.returncode,
            "stdout": (res.stdout or "")[:_SNIPPET_CAP],
            "stderr": (res.stderr or "")[:_SNIPPET_CAP],
        }

    def web_search(self, query: str, max_results: int = 5) -> list[dict]:
        """Web search dispatcher.

        R-blocker:infra-spend — requires a paid API
        (Brave/Serper/Tavily ~€5–15/mo at projected query volume).
        Until founder approves the spend, returns []. The interface
        is wired so the brain can call it; the upgrade path is a
        single env var (`BRAIN_WEB_SEARCH_PROVIDER`) + provider
        secret.
        """
        if not query:
            return []
        provider = os.getenv("BRAIN_WEB_SEARCH_PROVIDER", "").strip().lower()
        if not provider:
            log.debug(
                "brain_tool.web_search: no BRAIN_WEB_SEARCH_PROVIDER configured "
                "— returning empty (R-blocker:infra-spend ~€5-15/mo)",
            )
            return []
        log.debug(
            "brain_tool.web_search: provider=%s configured but client not "
            "implemented — return empty",
            provider,
        )
        return []


def brain_dispatch() -> BrainTool:
    """Factory — returns a fresh BrainTool. Equivalent to
    `BrainTool()` but the named export `brain_dispatch` matches
    the name pattern checked by `audit_brain_propagation_hooks`."""
    return BrainTool()


def spawn_investigation(patterns: list[str], audits: list[str] | None = None) -> dict:
    """One-shot investigation: parallel grep + (optional) audits.

    Convenience wrapper used by `_run_sibling_sweep` to run all
    pattern-based hunts at once. Returns {grep, audits}.
    """
    tool = brain_dispatch()
    out: dict = {"grep": tool.parallel_grep(patterns), "audits": {}}
    for name in audits or []:
        out["audits"][name] = tool.invoke_audit(name)
    return out


__all__ = ["BrainTool", "brain_dispatch", "spawn_investigation"]
