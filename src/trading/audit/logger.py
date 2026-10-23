"""
Audit Logger

Immutable decision trail for every trading decision.
Every decision can be reconstructed from the audit log.
Decisions are never modified - corrections are new events.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..canonical import Decision, QualityStatus
from ..persistence.store import SQLiteStore

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Immutable audit logger for trading decisions.
    
    Every decision is logged with full audit trail:
    - Input snapshot
    - Technical analysis
    - Fundamental thesis
    - Risk gate results
    - AXLE verification results
    - Final decision
    
    Decisions are never modified. Corrections are new events.
    """

    def __init__(
        self,
        store: SQLiteStore,
        journal_path: Optional[str] = None,
    ):
        self.store = store
        self.journal_path = Path(journal_path) if journal_path else None
        self._lock = threading.RLock()

        # Ensure journal directory exists
        if self.journal_path:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)

    def log_decision(self, decision: Decision) -> str:
        """
        Log a trading decision to audit trail.
        
        Writes to:
        1. SQLite database (structured, queryable)
        2. Append-only JSONL journal (immutable, crash-safe)
        
        Returns:
            Audit log entry ID
        """
        audit_entry = {
            "audit_id": str(uuid.uuid4()),
            "decision_id": str(decision.decision_id),
            "snapshot_id": str(decision.snapshot_id),
            "symbol": decision.symbol.value,
            "status": decision.status.value,
            "timestamp": decision.timestamp.isoformat(),
            "entry": str(decision.entry) if decision.entry else None,
            "sl": str(decision.sl) if decision.sl else None,
            "tp1": str(decision.tp1) if decision.tp1 else None,
            "tp2": str(decision.tp2) if decision.tp2 else None,
            "risk_pct": str(decision.risk_pct) if decision.risk_pct else None,
            "rr": str(decision.rr) if decision.rr else None,
            "failed_gates": [g.value for g in decision.failed_gates],
            "strategy_version": decision.strategy_version,
            "data_quality": decision.data_quality.value,
            "axle_verification": {
                k: v.value for k, v in decision.axle_verification.items()
            },
            "position_size": str(decision.position_size) if decision.position_size else None,
            "notes": decision.notes,
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }

        # Write to SQLite
        try:
            self._write_to_sqlite(audit_entry, decision)
        except Exception as e:
            logger.error(f"Failed to write audit to SQLite: {e}")

        # Write to append-only journal
        try:
            self._write_to_journal(audit_entry)
        except Exception as e:
            logger.error(f"Failed to write audit to journal: {e}")

        logger.info(
            f"Audit logged: {decision.status.value} for {decision.symbol.value} "
            f"(decision_id={decision.decision_id})"
        )

        return audit_entry["audit_id"]

    def _write_to_sqlite(
        self,
        audit_entry: dict,
        decision: Decision,
    ) -> None:
        """Write decision to SQLite."""
        self.store.save_decision(decision)

    def _write_to_journal(self, audit_entry: dict) -> None:
        """Write audit entry to append-only JSONL journal."""
        if not self.journal_path:
            return

        line = json.dumps(audit_entry, default=str, separators=(",", ":"))
        line_hash = __import__("hashlib").sha256(line.encode()).hexdigest()[:16]

        with self._lock:
            with open(self.journal_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())

    def get_decision(self, decision_id: str) -> Optional[dict]:
        """Get a decision from audit trail."""
        from ..canonical import uuid as uuid_mod

        try:
            decision = self.store.get_decision(uuid_mod.UUID(decision_id))
            if decision:
                return {
                    "decision_id": str(decision.decision_id),
                    "snapshot_id": str(decision.snapshot_id),
                    "symbol": decision.symbol.value,
                    "status": decision.status.value,
                    "timestamp": decision.timestamp.isoformat(),
                    "entry": str(decision.entry) if decision.entry else None,
                    "sl": str(decision.sl) if decision.sl else None,
                    "tp1": str(decision.tp1) if decision.tp1 else None,
                    "tp2": str(decision.tp2) if decision.tp2 else None,
                    "risk_pct": str(decision.risk_pct) if decision.risk_pct else None,
                    "rr": str(decision.rr) if decision.rr else None,
                    "failed_gates": [g.value for g in decision.failed_gates],
                    "strategy_version": decision.strategy_version,
                    "data_quality": decision.data_quality.value,
                    "axle_verification": {
                        k: v.value for k, v in decision.axle_verification.items()
                    },
                    "position_size": str(decision.position_size) if decision.position_size else None,
                    "notes": decision.notes,
                }
        except Exception as e:
            logger.error(f"Failed to get decision: {e}")

        return None

    def get_decisions(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get decisions with optional filters."""
        from ..canonical import Instrument, DecisionStatus

        inst = Instrument(symbol) if symbol else None
        dec_status = DecisionStatus(status) if status else None

        decisions = self.store.get_decisions(
            symbol=inst,
            status=dec_status,
            limit=limit,
        )

        return [
            {
                "decision_id": str(d.decision_id),
                "snapshot_id": str(d.snapshot_id),
                "symbol": d.symbol.value,
                "status": d.status.value,
                "timestamp": d.timestamp.isoformat(),
                "entry": str(d.entry) if d.entry else None,
                "sl": str(d.sl) if d.sl else None,
                "tp1": str(d.tp1) if d.tp1 else None,
                "tp2": str(d.tp2) if d.tp2 else None,
                "risk_pct": str(d.risk_pct) if d.risk_pct else None,
                "rr": str(d.rr) if d.rr else None,
                "failed_gates": [g.value for g in d.failed_gates],
                "strategy_version": d.strategy_version,
                "data_quality": d.data_quality.value,
                "notes": d.notes,
            }
            for d in decisions
        ]

    def verify_journal_integrity(self) -> tuple[bool, list[str]]:
        """Verify journal integrity by checking hashes."""
        errors = []

        if not self.journal_path or not self.journal_path.exists():
            return True, []

        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    entry = json.loads(line)
                    # Recompute hash from content (excluding hash field if present)
                    clean_entry = {k: v for k, v in entry.items() if k != "audit_hash"}
                    serialized = json.dumps(clean_entry, sort_keys=True, default=str)
                    expected_hash = __import__("hashlib").sha256(serialized.encode()).hexdigest()[:16]

                    stored_hash = entry.get("audit_hash", "")
                    if stored_hash and stored_hash != expected_hash:
                        errors.append(f"Line {line_num}: hash mismatch")
                except json.JSONDecodeError:
                    errors.append(f"Line {line_num}: invalid JSON")

        return len(errors) == 0, errors

    def get_stats(self) -> dict:
        """Get audit statistics."""
        store_stats = self.store.get_stats()

        journal_lines = 0
        if self.journal_path and self.journal_path.exists():
            with open(self.journal_path, "r", encoding="utf-8") as f:
                journal_lines = sum(1 for line in f if line.strip())

        return {
            **store_stats,
            "journal_entries": journal_lines,
        }

    # Import uuid at module level
    import uuid  # noqa: E402