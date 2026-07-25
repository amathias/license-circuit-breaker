"""Command-line entry points for seed, reset, and the vertical slice.

    python -m demo.cli seed
    python -m demo.cli reset
    python -m demo.cli slice      # read context, plan, reversible writeback
    python -m demo.cli verify     # verify the receipt ledger's hash chain

Against ``APP_ENV=offline`` these run on the in-memory fake and everything they
produce is marked ``simulated``. Only a run against the shared DataHub instance
produces live evidence, and this CLI never claims otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from app.clients import build_client, is_offline
from app.config import get_settings
from app.namespace import NamespaceViolation
from app.receipts import ReceiptLedger
from app.rights import License, Purpose, RightsEvent, RightsState
from app.workflow import build_impact_plan, perform_reversible_writeback
from demo.graph import REPLACEMENT_SOURCE, SOURCE
from demo.seed import SeedError, VerificationError, reset, restore, seed


def demo_rights_event() -> RightsEvent:
    """The rights event the demo revokes.

    Training and retrieval are removed; analytics is retained, which is what makes
    the unaffected branch provable rather than asserted.
    """
    return RightsEvent(
        event_id="evt-lcb-demo-001",
        effective_at=datetime.now(UTC),
        source_urn=SOURCE,
        prior=License(
            license_id="PARTNER-2026-01",
            name="Partner review feed agreement",
            permitted_purposes=frozenset(
                {Purpose.TRAINING, Purpose.RETRIEVAL, Purpose.ANALYTICS}
            ),
        ),
        new=License(
            license_id="PARTNER-2026-01",
            name="Partner review feed agreement",
            permitted_purposes=frozenset({Purpose.ANALYTICS}),
            state=RightsState.RESTRICTED,
        ),
        reason="Partner revoked training and retrieval rights effective immediately",
        replacement_source_urn=REPLACEMENT_SOURCE,
        requester="governance@example.com",
    )


def _report(label: str, simulated: bool) -> None:
    mode = "SIMULATED (in-memory fake)" if simulated else "LIVE (shared DataHub)"
    print(f"[{label}] mode: {mode}")


def cmd_seed(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = build_client(settings)
    _report("seed", is_offline(settings))

    try:
        result = seed(client, settings.namespace)
    except VerificationError as exc:
        # Emitting without verifying would report success for writes that never
        # landed, so a failed reread is a failed seed.
        print(f"Seed verification failed: {exc}", file=sys.stderr)
        return 4
    except SeedError as exc:
        print(f"Seed refused: {exc}", file=sys.stderr)
        return 2
    except NamespaceViolation as exc:
        print(f"Seed refused by namespace guard: {exc}", file=sys.stderr)
        return 3

    print(f"Seeded {result.count} entities under prefix {settings.datahub_urn_prefix!r}")
    print(f"Verified: {len(result.verified_entities)} entities, {len(result.verified_edges)} edges")
    print(f"Sentinel: {result.sentinel_urn}")
    print(f"Marker:   {result.marker}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = build_client(settings)
    _report("reset", is_offline(settings))

    try:
        result = reset(client, settings.namespace)
    except SeedError as exc:
        print(f"Reset refused: {exc}", file=sys.stderr)
        return 2
    except NamespaceViolation as exc:
        print(f"Reset refused by namespace guard: {exc}", file=sys.stderr)
        return 3

    print(f"Reset: {result.describe()}")
    if result.failed:
        for urn, reason in result.failed:
            print(f"  FAILED {urn}: {reason}", file=sys.stderr)
        return 5
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = build_client(settings)
    _report("restore", is_offline(settings))

    result = restore(client, settings.namespace)
    print(f"Restored {result.count} entities")
    if result.failed:
        for urn, reason in result.failed:
            print(f"  FAILED {urn}: {reason}", file=sys.stderr)
        return 5
    return 0


def cmd_slice(args: argparse.Namespace) -> int:
    """Run the vertical slice: context -> plan -> reversible writeback."""
    settings = get_settings()
    simulated = is_offline(settings)
    client = build_client(settings)
    ledger = ReceiptLedger(settings.ensure_state_dir())
    _report("slice", simulated)

    event = demo_rights_event()
    plan = build_impact_plan(client, event, settings.namespace, ledger=ledger, simulated=simulated)

    print(f"\nImpact plan for {event.source_urn}")
    print(f"  decisions:   {len(plan.decisions)}")
    print(f"  escalations: {len(plan.escalations)}")
    print(f"  destructive: {len(plan.destructive)}")
    print(f"  all clear:   {plan.all_clear}")
    print()
    for decision in plan.decisions:
        actions = ", ".join(a.value for a in decision.actions)
        rules = ", ".join(decision.rule_ids)
        print(f"  [{decision.priority:3d}] {actions:<18} {rules:<10} {decision.descendant_urn}")

    receipt = perform_reversible_writeback(
        client, event.source_urn, settings.namespace, ledger=ledger, simulated=simulated
    )
    print(
        f"\nWriteback: started={receipt.started} verified={receipt.verified} "
        f"restored={receipt.restored}"
    )
    print(f"  {receipt.detail}")

    if receipt.residual_risk:
        print(
            "  WARNING: the write may have landed and was not restored. "
            "The shared instance may retain state.",
            file=sys.stderr,
        )

    if args.output:
        payload = {
            "simulated": simulated,
            "event": json.loads(event.model_dump_json()),
            "decisions": [json.loads(d.model_dump_json()) for d in plan.decisions],
        }
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        print(f"\nWrote plan to {args.output}")

    # A writeback that was not both verified and restored is not a successful
    # slice, even though the plan above may be perfectly good. Exiting zero here
    # would let CI and the coordinator's promotion check treat a dirty shared
    # instance as a pass.
    if not receipt.clean:
        print(
            f"\nSlice FAILED: writeback verified={receipt.verified} "
            f"restored={receipt.restored}",
            file=sys.stderr,
        )
        return 6

    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    settings = get_settings()
    ledger = ReceiptLedger(settings.ensure_state_dir())
    ok, detail = ledger.verify_chain()
    print(f"Receipt ledger: {'INTACT' if ok else 'TAMPERED'} -- {detail}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo.cli", description="License Circuit Breaker demo")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="create the demo graph").set_defaults(func=cmd_seed)
    sub.add_parser("reset", help="soft-remove only entities this project seeded").set_defaults(
        func=cmd_reset
    )
    sub.add_parser("restore", help="reverse a soft reset").set_defaults(func=cmd_restore)
    slice_parser = sub.add_parser("slice", help="run the vertical slice")
    slice_parser.add_argument("--output", help="write the plan to a JSON file")
    slice_parser.set_defaults(func=cmd_slice)
    sub.add_parser("verify", help="verify the receipt ledger chain").set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
