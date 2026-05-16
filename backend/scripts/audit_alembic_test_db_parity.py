#!/usr/bin/env python3
"""audit_alembic_test_db_parity.py — ensure wishspark_test is at head.

Problem class
-------------
A silent divergence between prod DB's alembic_version and the test DB's
`wishspark_test.alembic_version` is easy to introduce and painful to
debug:
  - new migration added → prod upgraded → test DB forgotten
  - test-suite passes locally (test DB at head) but fails in CI
  - programmatic `alembic upgrade head` silently targets prod because
    env.py clobbers Config overrides (fixed 2026-04-23; but the CLASS
    of bug — schema drift going undetected — deserves a permanent gate)

What this audit does
--------------------
1. Reads alembic_version from BOTH wishspark and wishspark_test.
2. Uses alembic's own ScriptDirectory to resolve the single head revision.
3. Computes a schema FINGERPRINT for each DB (sorted list of
   table.column.type) and compares — so manual-DDL drift (ALTER TABLE
   bypassing alembic) is detected even when alembic_version matches.
4. Fails (exit 1 in --strict) if:
   - wishspark_test.alembic_version != head (stale test DB)
   - OR the two DBs alembic versions disagree (divergent state)
   - OR the two DBs' schema fingerprints disagree (manual DDL drift)
   - OR wishspark_test is missing the alembic_version table entirely

2026-04-23 DA hardening: added schema-fingerprint comparison. Before
this, only alembic_version rows were checked — but on the same day a
manual `ALTER TABLE bugfix_candidates ADD COLUMN proposal_model` +
`UPDATE alembic_version` on wishspark_test would have been invisible
if I'd forgotten to apply it to prod too. Fingerprint catches that.

Orthogonal to `alembic check` (which compares prod DB vs models). This
audit specifically guards test-vs-prod parity — the class of bug where
a new migration was applied to prod but forgotten on test, OR where
a dev's fix-for-test manual DDL diverges from migration source.

Runs as part of preflight.sh. Does NOT attempt to auto-upgrade — that's
conftest.py's job at test-session start. This audit is the pre-commit
gate: "if you forgot to run tests locally, you can't commit drift."

Env
---
  DATABASE_URL            — source of truth for prod URL
  DATABASE_URL_TEST       — optional explicit test URL (else derived
                             by swapping /wishspark → /wishspark_test)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = REPO_ROOT / "migrations" / "versions"


def _load_env():
    """Best-effort .env loader so the audit works outside pytest."""
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except Exception:
            pass


def _resolve_urls() -> tuple[str, str]:
    prod = os.environ.get("DATABASE_URL")
    if not prod:
        raise RuntimeError("DATABASE_URL must be set")
    test_url = os.environ.get("DATABASE_URL_TEST")
    if not test_url:
        test_url = re.sub(r"/wishspark(\?|$)", r"/wishspark_test\1", prod)
    return prod, test_url


def _db_version(url: str) -> str | None:
    from sqlalchemy import create_engine, text
    eng = create_engine(url, pool_pre_ping=True)
    try:
        with eng.connect() as c:
            row = c.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
            return row[0] if row else None
    except Exception as exc:
        # Table missing or connection failure — return sentinel
        return f"__error__:{type(exc).__name__}:{str(exc)[:120]}"
    finally:
        eng.dispose()


def _db_schema_fingerprint(url: str) -> tuple[str, list[str]]:
    """Return (fingerprint_hash, sorted list of 'table.column:type')
    for the public schema. Excludes alembic_version itself (tracked
    separately), Postgres-managed catalog tables, and EXTENSION-owned
    relations (e.g. the pg_stat_statements view, created in prod by the
    2026-05-16 truth-infra restart but absent from the test DB). Alembic
    neither creates nor manages extension objects, so a per-environment
    extension difference is NOT app-schema drift — counting it produced
    a false-positive parity failure on every preflight. Excluding all
    `pg_depend deptype='e'` relations closes the whole class (any
    extension, not just this one), not the single symptom.

    Fingerprint is a SHA256 of the concatenated sorted column list —
    stable across Postgres minor versions + independent of column
    creation order. Used to detect manual-DDL drift between DBs that
    report the same alembic version.
    """
    import hashlib
    from sqlalchemy import create_engine, text
    eng = create_engine(url, pool_pre_ping=True)
    try:
        with eng.connect() as c:
            rows = c.execute(text("""
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name NOT IN ('alembic_version')
                  AND table_name NOT IN (
                      SELECT c.relname
                      FROM pg_class c
                      JOIN pg_depend d
                        ON d.objid = c.oid
                       AND d.classid = 'pg_class'::regclass
                       AND d.deptype = 'e'
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                      WHERE n.nspname = 'public'
                  )
                ORDER BY table_name, column_name
            """)).fetchall()
        triples = [f"{r[0]}.{r[1]}:{r[2]}" for r in rows]
        joined = "|".join(triples)
        fp = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
        return fp, triples
    except Exception as exc:
        return f"__error__:{type(exc).__name__}", []
    finally:
        eng.dispose()


def _resolve_head_via_alembic() -> str | None:
    """Ask alembic itself for the current head revision.

    Uses ScriptDirectory.get_current_head() which correctly handles dead
    branches (files whose down_revision points at a non-existent revision
    are treated as disconnected, not as alternate heads). Returns None
    if multiple heads exist (merge pending — a different problem class).
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception:
        return None


def _autofix_test_db(test_url: str) -> int:
    """Deterministic auto-fix: run `alembic upgrade head` against the
    test DB. Safe scope: only operates on wishspark_test (never prod).
    Returns subprocess exit code (0 = success).
    """
    import subprocess
    backend_dir = "/opt/wishspark/backend"
    cmd = [f"{backend_dir}/venv/bin/alembic", "upgrade", "head"]
    env = {"DATABASE_URL": test_url, "PATH": "/usr/bin:/bin:/usr/local/bin"}
    # Inherit minimal env to keep secrets from leaking into subprocess
    import os
    for k in ("MERCHANT_SESSION_SECRET", "MERCHANT_TOKEN_ENCRYPTION_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        if v := os.environ.get(k):
            env[k] = v
    try:
        result = subprocess.run(
            cmd, cwd=backend_dir, env=env, capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print("auto-fix: alembic upgrade head succeeded on wishspark_test")
            return 0
        print(f"auto-fix: alembic upgrade failed exit={result.returncode}")
        print(result.stdout[-300:])
        print(result.stderr[-300:], file=sys.stderr)
        return result.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"auto-fix: alembic invocation failed — {e}", file=sys.stderr)
        return 1


@telemetered("audit_alembic_test_db_parity")
def main() -> int:
    strict = "--strict" in sys.argv
    fix_mode = "--fix" in sys.argv

    _load_env()
    try:
        prod_url, test_url = _resolve_urls()
    except Exception as exc:
        print(f"✗ {exc}")
        return 1 if strict else 0

    file_head = _resolve_head_via_alembic()
    prod_ver = _db_version(prod_url)
    test_ver = _db_version(test_url)

    # Connection / table failures
    if isinstance(test_ver, str) and test_ver.startswith("__error__"):
        print(f"✗ wishspark_test alembic_version unreadable: {test_ver.split(':',1)[1]}")
        print(f"  → run: DATABASE_URL={test_url} ./venv/bin/alembic upgrade head")
        return 1 if strict else 0
    if isinstance(prod_ver, str) and prod_ver.startswith("__error__"):
        print(f"⚠ wishspark alembic_version unreadable (prod): {prod_ver.split(':',1)[1]}")
        print("  audit cannot compare parity; skipping non-fatally")
        return 0

    failures: list[str] = []
    if file_head and prod_ver and prod_ver != file_head:
        failures.append(
            f"prod wishspark@{prod_ver} is behind file head {file_head}"
        )
    if file_head and test_ver and test_ver != file_head:
        failures.append(
            f"test wishspark_test@{test_ver} is behind file head {file_head}"
        )
    if prod_ver and test_ver and prod_ver != test_ver:
        failures.append(
            f"prod@{prod_ver} != test@{test_ver} — DBs disagree"
        )

    # Schema fingerprint — catches manual DDL drift even when alembic
    # versions match (the class of bug introduced when someone does
    # `ALTER TABLE ... UPDATE alembic_version` on one DB but forgets
    # the other).
    prod_fp, prod_cols = _db_schema_fingerprint(prod_url)
    test_fp, test_cols = _db_schema_fingerprint(test_url)
    if prod_fp.startswith("__error__") or test_fp.startswith("__error__"):
        # Skip fingerprint check but warn — shouldn't block the run.
        print(f"⚠ schema fingerprint probe failed: prod={prod_fp} test={test_fp}")
    elif prod_fp != test_fp:
        # Compute a human-readable diff so the remediation is obvious.
        prod_set = set(prod_cols)
        test_set = set(test_cols)
        only_prod = sorted(prod_set - test_set)
        only_test = sorted(test_set - prod_set)
        failures.append(
            f"schema drift: prod_fp={prod_fp} test_fp={test_fp}"
        )
        if only_prod:
            failures.append(
                f"  prod-only columns (test DB missing {len(only_prod)}): "
                + ", ".join(only_prod[:5])
                + (f" ... +{len(only_prod)-5} more" if len(only_prod) > 5 else "")
            )
        if only_test:
            failures.append(
                f"  test-only columns (prod DB missing {len(only_test)}): "
                + ", ".join(only_test[:5])
                + (f" ... +{len(only_test)-5} more" if len(only_test) > 5 else "")
            )

    if failures:
        # Auto-fix: ONLY for the test-DB-behind-head case (deterministic
        # alembic upgrade). Schema fingerprint drift requires human
        # judgment (delete column? add migration?) and is NOT auto-fixed.
        version_only_drift = (
            file_head and test_ver and test_ver != file_head
            and not any("schema drift" in f for f in failures)
        )
        if fix_mode and version_only_drift:
            rc = _autofix_test_db(test_url)
            if rc != 0:
                return rc
            # Re-check after upgrade
            new_test_ver = _db_version(test_url)
            if new_test_ver == file_head:
                print(f"auto-fix: wishspark_test now at head {file_head}")
                return 0
            print(f"auto-fix: upgrade ran but version still {new_test_ver} (expected {file_head})")
            return 1
        if fix_mode:
            print("auto-fix: not applied — schema fingerprint drift requires manual DDL review")
        print("✗ alembic test-DB parity violated:")
        for f in failures:
            print(f"  - {f}")
        print()
        print("Remediation:")
        print(f"  DATABASE_URL={test_url} ./venv/bin/alembic upgrade head")
        print("  OR run this audit with --fix (deterministic version-drift only)")
        print("  For schema-drift: if the drift is from manual DDL, drop the")
        print("  extra columns OR create an alembic migration that brings")
        print("  both DBs to the same fingerprint.")
        return 1 if strict else 0

    print(f"✓ alembic parity: prod={prod_ver} test={test_ver} head={file_head} "
          f"schema_fp={prod_fp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
