#!/usr/bin/env python3
"""
run_simulation.py — CLI for synthetic merchant simulation.

Usage:
    # Create synthetic merchants (one-time setup)
    python -m app.scripts.run_simulation create --count 6

    # Run one simulation cycle (generates 1 hour of activity)
    python -m app.scripts.run_simulation run

    # Run a stress scenario (3x volume)
    python -m app.scripts.run_simulation run --scenario stress

    # Run with specific hours of history
    python -m app.scripts.run_simulation run --hours 4

    # Check simulation status
    python -m app.scripts.run_simulation status

    # Clean up all synthetic data
    python -m app.scripts.run_simulation cleanup

All synthetic data is permanently labeled and isolated from product learning.
"""
from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.services.simulation_engine import (
    create_synthetic_merchants,
    run_simulation_cycle,
    cleanup_synthetic_merchants,
    get_simulation_status,
    get_synthetic_merchants,
    ARCHETYPES,
)


def cmd_create(args):
    """Create synthetic merchants."""
    db = SessionLocal()
    try:
        archetypes = None
        if args.archetype:
            if args.archetype not in ARCHETYPES:
                print(f"Unknown archetype: {args.archetype}")
                print(f"Available: {', '.join(ARCHETYPES.keys())}")
                sys.exit(1)
            archetypes = [args.archetype]

        shops = create_synthetic_merchants(db, count=args.count, archetypes=archetypes)
        db.commit()
        print(f"Created {len(shops)} synthetic merchants:")
        for s in shops:
            print(f"  {s}")
    except Exception as exc:
        db.rollback()
        print(f"Error: {exc}")
        sys.exit(1)
    finally:
        db.close()


def cmd_run(args):
    """Run a simulation cycle."""
    db = SessionLocal()
    try:
        summary = run_simulation_cycle(
            db,
            scenario=args.scenario,
            hours=args.hours,
            seed=args.seed,
        )
        db.commit()
        print(f"\nSimulation cycle complete:")
        print(f"  Merchants active:    {summary.merchants_active}")
        print(f"  Events generated:    {summary.events_generated}")
        print(f"  Events dropped:      {summary.events_failed}")
        print(f"  Purchases generated: {summary.purchases_generated}")
        print(f"  Alerts generated:    {summary.alerts_generated}")
        print(f"  Scenario:            {', '.join(summary.scenarios_run)}")
        if summary.errors:
            print(f"  Errors:")
            for e in summary.errors:
                print(f"    - {e}")
    except Exception as exc:
        db.rollback()
        print(f"Error: {exc}")
        sys.exit(1)
    finally:
        db.close()


def cmd_status(args):
    """Show simulation status."""
    db = SessionLocal()
    try:
        status = get_simulation_status(db)
        print(f"\nSimulation Status:")
        print(f"  Synthetic merchants:  {status['synthetic_merchants']}")
        print(f"  Isolation mode:       {status['isolation_mode']}")
        print(f"  Synthetic events:     {status.get('synthetic_events', 0)}")
        print(f"  Synthetic alerts:     {status.get('synthetic_alerts', 0)}")
        print(f"  Synthetic metrics:    {status.get('synthetic_metrics', 0)}")
        print(f"  Synthetic signals:    {status.get('synthetic_signals', 0)}")
        print(f"  Synthetic candidates: {status.get('synthetic_candidates', 0)}")
        print(f"  Synthetic lessons:    {status.get('synthetic_lessons', 0)}")
        if status.get("merchants"):
            print(f"\n  Merchants:")
            for m in status["merchants"]:
                print(f"    {m['shop_domain']} ({m['archetype']})")
    finally:
        db.close()


def cmd_cleanup(args):
    """Remove all synthetic data."""
    db = SessionLocal()
    try:
        if not args.confirm:
            merchants = get_synthetic_merchants(db)
            if merchants:
                print(f"This will delete {len(merchants)} synthetic merchants and all their data.")
                print("Shops:")
                for m in merchants:
                    print(f"  {m['shop_domain']}")
                print("\nRun with --confirm to proceed.")
                return
            else:
                print("No synthetic merchants found.")
                return

        result = cleanup_synthetic_merchants(db)
        db.commit()
        print(f"\nCleanup complete:")
        for key, value in result.items():
            if key != "shops":
                print(f"  {key}: {value}")
    except Exception as exc:
        db.rollback()
        print(f"Error: {exc}")
        sys.exit(1)
    finally:
        db.close()


def cmd_probe(args):
    """Run HTTP ingestion probe."""
    db = SessionLocal()
    try:
        from app.services.simulation_probe import run_ingestion_probe
        result = run_ingestion_probe(db, base_url=args.url)
        status = "PASS" if result.all_passed else "FAIL"
        print(f"\nIngestion Probe: {status}")
        print(f"  Checks: {result.checks_passed}/{result.checks_run} passed")
        if result.latency_ms:
            print(f"  Latency:")
            for name, ms in result.latency_ms.items():
                print(f"    {name}: {ms}ms")
        if result.failures:
            print(f"  Failures:")
            for f in result.failures:
                print(f"    [{f['check']}] {f['detail']}")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Synthetic merchant simulation for operational hardening",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create synthetic merchants")
    p_create.add_argument("--count", type=int, default=6, help="Number of merchants (default: 6)")
    p_create.add_argument("--archetype", type=str, help="Specific archetype to use")

    # run
    p_run = sub.add_parser("run", help="Run simulation cycle")
    p_run.add_argument("--scenario", default="mixed",
                        choices=["mixed", "healthy_only", "stress", "failure"],
                        help="Scenario to run (default: mixed)")
    p_run.add_argument("--hours", type=int, default=1, help="Hours of activity to simulate (default: 1)")
    p_run.add_argument("--seed", type=int, help="Random seed for reproducibility")

    # status
    sub.add_parser("status", help="Show simulation status")

    # probe
    p_probe = sub.add_parser("probe", help="Run HTTP ingestion probe")
    p_probe.add_argument("--url", default="http://127.0.0.1:8000",
                          help="Backend base URL (default: http://127.0.0.1:8000)")

    # cleanup
    p_cleanup = sub.add_parser("cleanup", help="Remove all synthetic data")
    p_cleanup.add_argument("--confirm", action="store_true", help="Confirm deletion")

    args = parser.parse_args()

    commands = {
        "create": cmd_create,
        "run": cmd_run,
        "status": cmd_status,
        "probe": cmd_probe,
        "cleanup": cmd_cleanup,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
