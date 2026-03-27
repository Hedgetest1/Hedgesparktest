from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


SANDBOX_ROOT = Path("/opt/wishspark/sandbox")
SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)


def _ts() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _safe_name(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in {"_", "-"} else "_" for c in value.lower())
    return cleaned[:80] or "task"


def create_sandbox_run(goal: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}

    run_id = f"{_ts()}_{_safe_name(goal)}"
    run_path = SANDBOX_ROOT / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    summary_path = run_path / "summary.txt"
    payload_path = run_path / "payload.json"
    status_path = run_path / "status.txt"

    summary_path.write_text(
        f"WishSpark Sandbox Run\n"
        f"run_id: {run_id}\n"
        f"goal: {goal}\n"
        f"created_at_utc: {datetime.utcnow().isoformat()}Z\n",
        encoding="utf-8",
    )

    try:
        import json
        payload_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        payload_path.write_text(str(payload), encoding="utf-8")

    status_path.write_text("created", encoding="utf-8")

    return {
        "run_id": run_id,
        "goal": goal,
        "sandbox_path": str(run_path),
        "status": "created",
    }


def update_sandbox_status(run_id: str, status: str) -> dict[str, Any]:
    run_path = SANDBOX_ROOT / run_id
    if not run_path.exists():
        return {
            "run_id": run_id,
            "status": "missing",
            "error": "sandbox run not found",
        }

    status_path = run_path / "status.txt"
    status_path.write_text(status, encoding="utf-8")

    return {
        "run_id": run_id,
        "status": status,
        "sandbox_path": str(run_path),
    }


def inspect_sandbox_run(run_id: str) -> dict[str, Any]:
    run_path = SANDBOX_ROOT / run_id
    if not run_path.exists():
        return {
            "run_id": run_id,
            "status": "missing",
            "error": "sandbox run not found",
        }

    files = sorted([p.name for p in run_path.iterdir() if p.is_file()])

    status = "unknown"
    status_path = run_path / "status.txt"
    if status_path.exists():
        status = status_path.read_text(encoding="utf-8").strip()

    return {
        "run_id": run_id,
        "sandbox_path": str(run_path),
        "status": status,
        "files": files,
    }
