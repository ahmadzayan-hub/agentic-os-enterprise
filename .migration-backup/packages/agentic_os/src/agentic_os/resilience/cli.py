"""Operator entry point for the disaster recovery exercise.

agentic-dr run --environment staging --executed-by "ops@rta"
agentic-dr latest
"""

from __future__ import annotations

import argparse
import json
import sys

from agentic_os.resilience.backup import RestoreNotConfigured, latest_result, run_exercise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic-dr", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="dump, restore into a scratch database and verify")
    run.add_argument("--environment", default="test")
    run.add_argument("--executed-by", default="")
    run.add_argument(
        "--discard-artifact",
        action="store_true",
        help="delete the dump once the restore has been verified",
    )

    sub.add_parser("latest", help="show the most recent recorded exercise")

    args = parser.parse_args(argv)

    if args.command == "latest":
        result = latest_result()
        if result is None:
            print("no restore exercise has been recorded")
            return 1
        print(json.dumps(result, indent=2, default=str))
        return 0

    try:
        outcome = run_exercise(
            environment=args.environment,
            executed_by=args.executed_by,
            keep_artifact=not args.discard_artifact,
        )
    except RestoreNotConfigured as exc:
        print(f"NOT_RUN: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(outcome.to_dict(), indent=2))
    return 0 if outcome.outcome == "SUCCESS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
