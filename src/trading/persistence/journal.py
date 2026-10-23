"""
Append-Only Event Journal

Crash-safe, append-only event log for raw market data.
Each line is a complete JSON event with integrity hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Any

from ..canonical import RawEvent, RawTickEvent, RawOHLCEvent, RawNewsEvent, RawCalendarEvent


class EventJournal:
    """
    Append-only event journal with crash recovery.
    
    Features:
    - JSON Lines format (one event per line)
    - SHA256 hash per line for integrity
    - Automatic rotation by size/date
    - fsync on every write (configurable)
    - Thread-safe
    """
    
    def __init__(
        self,
        path: str,
        max_size_mb: int = 100,
        rotation: str = "daily",
        sync_on_write: bool = True,
    ):
        self.path = Path(path)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.rotation = rotation
        self.sync_on_write = sync_on_write
        
        self._lock = threading.RLock()
        self._file: Optional[Any] = None
        self._current_size = 0
        self._sequence = 0
        
        # Ensure directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        # Open or create journal
        self._open_journal()
        
        # Read last sequence for resume
        self._sequence = self._get_last_sequence()
    
    def _open_journal(self) -> None:
        """Open journal file for appending."""
        if self._file:
            self._file.close()
        
        self._file = open(self.path, "a", encoding="utf-8", buffering=1)
        self._current_size = self.path.stat().st_size if self.path.exists() else 0
    
    def _get_last_sequence(self) -> int:
        """Read last sequence number from journal."""
        if not self.path.exists():
            return 0
        
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                last_line = ""
                for line in f:
                    last_line = line.strip()
                
                if last_line:
                    event = json.loads(last_line)
                    return event.get("sequence", 0)
        except (json.JSONDecodeError, KeyError):
            pass
        
        return 0
    
    def _rotate_if_needed(self) -> None:
        """Rotate journal if size limit reached."""
        if self._current_size >= self.max_size_bytes:
            self._rotate()
    
    def _rotate(self) -> None:
        """Rotate journal file."""
        if self._file:
            self._file.close()
        
        # Rename current to timestamped backup
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_path = self.path.with_suffix(f".{timestamp}.log")
        self.path.rename(backup_path)
        
        # Create new journal
        self._open_journal()
        self._current_size = 0
    
    def _compute_hash(self, data: dict) -> str:
        """Compute SHA256 hash of event data (excluding hash field)."""
        # Remove hash field if present
        clean_data = {k: v for k, v in data.items() if k != "raw_payload_hash"}
        serialized = json.dumps(clean_data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]
    
    def append(self, event: RawEvent) -> int:
        """
        Append event to journal.
        Returns assigned sequence number.
        """
        with self._lock:
            self._rotate_if_needed()
            self._sequence += 1
            
            # Convert to dict
            if hasattr(event, "__dataclass_fields__"):
                event_dict = {
                    k: getattr(event, k) 
                    for k in event.__dataclass_fields__
                }
            else:
                event_dict = dict(event)
            
            # Add sequence and hash
            event_dict["sequence"] = self._sequence
            event_dict["persisted_ts"] = datetime.utcnow().isoformat() + "Z"
            event_dict["raw_payload_hash"] = self._compute_hash(event_dict)
            
            # Write line
            line = json.dumps(event_dict, default=str, separators=(",", ":"))
            self._file.write(line + "\n")
            self._current_size += len(line) + 1
            
            if self.sync_on_write:
                self._file.flush()
                os.fsync(self._file.fileno())
            
            return self._sequence
    
    def append_batch(self, events: list[RawEvent]) -> list[int]:
        """Append multiple events efficiently."""
        sequences = []
        with self._lock:
            for event in events:
                sequences.append(self.append(event))
        return sequences
    
    def replay(self, from_sequence: int = 0) -> Iterator[dict]:
        """
        Replay events from journal.
        Yields event dicts with sequence >= from_sequence.
        """
        if not self.path.exists():
            return
        
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    event = json.loads(line)
                    if event.get("sequence", 0) >= from_sequence:
                        yield event
                except json.JSONDecodeError:
                    continue
    
    def replay_raw(self, from_sequence: int = 0) -> Iterator[RawEvent]:
        """Replay events as RawEvent objects."""
        for event_dict in self.replay(from_sequence):
            yield self._dict_to_raw_event(event_dict)
    
    def _dict_to_raw_event(self, data: dict) -> RawEvent:
        """Convert dict back to appropriate RawEvent type."""
        event_type = data.get("event_type", "tick")
        
        # Convert ISO timestamps back to datetime
        for key in ["provider_ts", "received_ts", "persisted_ts"]:
            if key in data and isinstance(data[key], str):
                data[key] = datetime.fromisoformat(data[key].replace("Z", "+00:00"))
        
        if event_type == "tick":
            return RawTickEvent(**data)
        elif event_type == "ohlc":
            return RawOHLCEvent(**data)
        elif event_type == "news":
            return RawNewsEvent(**data)
        elif event_type == "calendar":
            return RawCalendarEvent(**data)
        
        return RawEvent(**data)
    
    def get_last_sequence(self) -> int:
        """Get last sequence number."""
        return self._sequence
    
    def verify_integrity(self) -> tuple[bool, list[str]]:
        """
        Verify journal integrity by checking hashes.
        Returns (is_valid, list_of_errors).
        """
        errors = []
        
        with open(self.path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                
                try:
                    event = json.loads(line)
                    stored_hash = event.get("raw_payload_hash")
                    if stored_hash:
                        computed_hash = self._compute_hash(event)
                        if stored_hash != computed_hash:
                            errors.append(f"Line {line_num}: hash mismatch")
                except json.JSONDecodeError:
                    errors.append(f"Line {line_num}: invalid JSON")
        
        return len(errors) == 0, errors
    
    def close(self) -> None:
        """Close journal file."""
        with self._lock:
            if self._file:
                self._file.flush()
                os.fsync(self._file.fileno())
                self._file.close()
                self._file = None
    
    def __enter__(self) -> "EventJournal":
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


class JournalReader:
    """Read-only journal access for replay."""
    
    def __init__(self, path: str):
        self.path = Path(path)
    
    def read_all(self) -> list[dict]:
        """Read all events as dicts."""
        events = []
        if not self.path.exists():
            return events
        
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return events
    
    def read_last_n(self, n: int) -> list[dict]:
        """Read last N events efficiently."""
        events = []
        if not self.path.exists():
            return events
        
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines[-n:]:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return events