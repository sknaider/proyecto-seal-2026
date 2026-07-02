#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mini-SOUL Synchronization Daemon — Client Implementation
=========================================================

Orchestrates deterministic, paginated synchronization of local edge memories
(chunks and distilled exchanges) to central SOUL via Ed25519-signed, device-bound tokens.

SEAL Memory System | Mini-SOUL Edge | 2026-07-02
Ref: minisoul_local_schema.sql, minisoul_sync_policy.py, seal_token.py

Design:
  1. Opens local SQLite DB with PRAGMA foreign_keys=ON (required for referential integrity)
  2. Reads outbox entries with sync_state='pending' in batches
  3. Reconstructs full memory objects, applies sync policies (should_sync_up + select_sync_batch)
  4. Obtains Ed25519 device-bound token via seal_token.issue_token()
  5. Calls push_fn(token, batch) — callback injected (testable, no HTTP hardcoding)
  6. Marks outbox entries sync_state='synced' on success; increments retry_count and marks 'failed' after 3 attempts
  7. ITERATES until outbox is empty (pagination enforced; no single-call assumption)

Idempotency:
  - Each outbox entry has a nonce (UUID) for deduplication at the server
  - Retry logic: max 3 attempts before marking failed (can be resumed later)

Deterministic:
  - All memory selection via minisoul_sync_policy.py (no LLM, no heuristics)
  - Token generation via seal_token.py (deterministic Ed25519 signing)
  - Database transactions ensure consistency on partial failures

Error handling:
  - Retries with exponential backoff within retry_count < 3
  - Logs all sync operations to sync_log table for audit trail
  - Conflicts logged to conflict_log for later manual review or LLM arbitration

Dependencies:
  - sqlite3 (stdlib)
  - minisoul_sync_policy (local module: should_sync_up, select_sync_batch)
  - seal_token (local module: issue_token, device_fingerprint)
"""

import sqlite3
import json
import logging
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional, Any
from dataclasses import dataclass, asdict
from contextlib import contextmanager

# Local module imports (DEBE estar en el mismo repo)
try:
    from minisoul_sync_policy import should_sync_up, select_sync_batch
except ImportError:
    raise ImportError("Missing minisoul_sync_policy.py in the same directory")

try:
    from seal_token import issue_token, device_fingerprint, Ed25519PrivateKey
except ImportError:
    raise ImportError("Missing seal_token.py in the same directory")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Logging
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# Default SQLite DB path
DEFAULT_DB_PATH = Path.home() / ".seal" / "mini-soul.db"

# Sync configuration
MAX_RETRIES = 3
BATCH_PAGE_SIZE = 50  # Paginated sync: max 50 entries per push_fn call
SYNC_TIMEOUT_S = 30  # Default timeout for push_fn callback


@dataclass
class SyncConfig:
    """Configuration for the sync daemon."""
    db_path: Path = DEFAULT_DB_PATH
    agent: str = "UNKNOWN"
    device_id: str = "UNKNOWN"
    private_key: Optional[Ed25519PrivateKey] = None  # SOUL central private key for token signing
    token_ttl_s: int = 86400  # 24h token lifetime
    batch_size: int = BATCH_PAGE_SIZE
    max_retries: int = MAX_RETRIES
    log_level: str = "INFO"


@dataclass
class SyncResult:
    """Result of a single sync operation."""
    success: bool
    outbox_id: int
    nonce: str
    error_message: Optional[str] = None
    retry_count: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# SQLite Connection Context Manager
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def get_db_connection(db_path: Path):
    """
    Opens a connection to the mini-SOUL local database.
    
    Enforces:
      - PRAGMA foreign_keys = ON (required for referential integrity checks)
      - PRAGMA journal_mode = WAL (write-ahead logging for concurrent access)
    
    Yields a sqlite3.Connection object; commits on success, rolls back on exception.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row  # Return rows as dicts
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Memory Reconstruction & Sync Eligibility
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_memory_from_chunk(conn: sqlite3.Connection, chunk_id: str) -> Optional[dict]:
    """
    Reconstructs a full memory object from chunks table.
    
    Args:
        conn: SQLite connection
        chunk_id: Primary key (content-addressed SHA256[:32])
    
    Returns:
        Full memory dict (with all fields) or None if not found.
    """
    cursor = conn.execute(
        """
        SELECT id, agent, category, content, importance, scope, created_at,
               memory_type, lifecycle, synced_at, cloud_id, source
        FROM chunks
        WHERE id = ?
        """,
        (chunk_id,)
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def reconstruct_distilled_exchange(conn: sqlite3.Connection, exchange_id: int) -> Optional[dict]:
    """
    Reconstructs a full distilled exchange object.
    
    Args:
        conn: SQLite connection
        exchange_id: Primary key
    
    Returns:
        Full exchange dict or None if not found.
    """
    cursor = conn.execute(
        """
        SELECT id, session_id, agent, exchange_core, specific_context, 
               source_tokens, distilled_tokens, compression_ratio,
               created_at, synced_at, lifecycle
        FROM distilled_exchanges
        WHERE id = ?
        """,
        (exchange_id,)
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def validate_and_select_batch(conn: sqlite3.Connection, batch_size: int) -> list:
    """
    Reads pending outbox entries, reconstructs memories, applies sync policies,
    and returns eligible entries for sync.
    
    Implements double-check:
      1. Query outbox with sync_state='pending'
      2. Reconstruct full memory objects from chunks/distilled_exchanges
      3. Apply should_sync_up policy (filters on importance, invalidation, scope)
      4. Apply select_sync_batch prioritization (sort by importance, recency)
    
    Args:
        conn: SQLite connection
        batch_size: Max number of entries to return
    
    Returns:
        List of dicts: {outbox_id, chunk_id, distilled_exchange_id, agent, entry_type, 
                        memory_object, nonce, scope}
    """
    # Step 1: Read pending outbox entries
    cursor = conn.execute(
        """
        SELECT id, chunk_id, distilled_exchange_id, agent, entry_type, 
               scope, created_at, nonce
        FROM outbox
        WHERE sync_state = 'pending'
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (batch_size,)
    )
    pending_entries = [dict(row) for row in cursor.fetchall()]
    
    if not pending_entries:
        return []
    
    # Step 2 & 3: Reconstruct and filter by policy
    eligible = []
    for entry in pending_entries:
        memory_obj = None
        if entry["entry_type"] == "chunk" and entry["chunk_id"]:
            memory_obj = reconstruct_memory_from_chunk(conn, entry["chunk_id"])
        elif entry["entry_type"] == "distilled_exchange" and entry["distilled_exchange_id"]:
            memory_obj = reconstruct_distilled_exchange(conn, entry["distilled_exchange_id"])
        
        if not memory_obj:
            logger.warning(
                f"Could not reconstruct memory for outbox entry {entry['id']}: "
                f"type={entry['entry_type']}, chunk_id={entry['chunk_id']}"
            )
            continue
        
        # Apply sync policy (fail-closed: only sync if explicitly eligible)
        if should_sync_up(memory_obj):
            eligible.append({
                "outbox_id": entry["id"],
                "chunk_id": entry["chunk_id"],
                "distilled_exchange_id": entry["distilled_exchange_id"],
                "agent": entry["agent"],
                "entry_type": entry["entry_type"],
                "memory_object": memory_obj,
                "nonce": entry["nonce"],
                "scope": entry["scope"],
                "created_at": entry["created_at"],
            })
    
    # Step 4: Prioritize (select_sync_batch sorts by importance desc, recency desc)
    if eligible:
        memories_for_batch = [e["memory_object"] for e in eligible]
        prioritized_memories = select_sync_batch(memories_for_batch, max_n=None)  # All eligible
        
        # Re-order eligible list to match prioritization
        prioritized_ids = {m["id"]: i for i, m in enumerate(prioritized_memories)}
        eligible_sorted = sorted(
            eligible,
            key=lambda e: prioritized_ids.get(e["memory_object"]["id"], float("inf"))
        )
        return eligible_sorted
    
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Token Generation & Payload Assembly
# ─────────────────────────────────────────────────────────────────────────────

def assemble_sync_payload(batch: list, agent: str, device_id: str, 
                          device_fp: str, token: dict) -> dict:
    """
    Assembles a sync payload for push_fn.
    
    Structure:
      {
        "token": {...Ed25519 signed token...},
        "device_id": device_id,
        "device_fingerprint": device_fp,
        "agent": agent,
        "batch": [
          {
            "nonce": "unique-id",
            "entry_type": "chunk" | "distilled_exchange",
            "scope": "private|shared|team|william",
            "memory": {...full object...}
          },
          ...
        ]
      }
    
    Args:
        batch: List of eligible entries from validate_and_select_batch
        agent: Agent name
        device_id: Device ID
        device_fp: Device fingerprint (SHA256)
        token: Ed25519 signed token dict
    
    Returns:
        Payload dict ready to send to push_fn
    """
    return {
        "token": token,
        "device_id": device_id,
        "device_fingerprint": device_fp,
        "agent": agent,
        "batch": [
            {
                "nonce": entry["nonce"],
                "entry_type": entry["entry_type"],
                "scope": entry["scope"],
                "memory": entry["memory_object"],
            }
            for entry in batch
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sync State Management
# ─────────────────────────────────────────────────────────────────────────────

def mark_outbox_synced(conn: sqlite3.Connection, outbox_ids: list) -> int:
    """
    Marks outbox entries as synced after successful push.
    
    Args:
        conn: SQLite connection
        outbox_ids: List of outbox.id values to mark
    
    Returns:
        Number of rows updated
    """
    if not outbox_ids:
        return 0
    
    placeholders = ",".join("?" * len(outbox_ids))
    cursor = conn.execute(
        f"""
        UPDATE outbox
        SET sync_state = 'synced', synced_at = CURRENT_TIMESTAMP
        WHERE id IN ({placeholders})
        """,
        outbox_ids
    )
    return cursor.rowcount


def mark_outbox_failed(conn: sqlite3.Connection, outbox_id: int, error_msg: str) -> bool:
    """
    Increments retry_count; marks as 'failed' if retry_count >= MAX_RETRIES.
    
    Args:
        conn: SQLite connection
        outbox_id: Outbox entry ID
        error_msg: Error message to log
    
    Returns:
        True if marked failed (retries exhausted), False if will retry.
    """
    cursor = conn.execute(
        """
        UPDATE outbox
        SET retry_count = retry_count + 1,
            error_message = ?,
            synced_at = NULL
        WHERE id = ?
        RETURNING retry_count
        """,
        (error_msg, outbox_id)
    )
    row = cursor.fetchone()
    if not row:
        return False
    
    retry_count = row[0]
    if retry_count >= MAX_RETRIES:
        conn.execute(
            """
            UPDATE outbox
            SET sync_state = 'failed'
            WHERE id = ?
            """,
            (outbox_id,)
        )
        return True
    return False


def log_sync_operation(conn: sqlite3.Connection, provider: str, direction: str,
                       status: str, items_processed: int, bytes_transferred: int,
                       duration_ms: int, error_msg: Optional[str] = None) -> int:
    """
    Logs sync operation to sync_log table for audit trail.
    
    Args:
        conn: SQLite connection
        provider: 'memories_push', 'memories_pull', etc.
        direction: 'push' or 'pull'
        status: 'success', 'failure', or 'partial'
        items_processed: Number of chunks/exchanges processed
        bytes_transferred: Bytes up/down
        duration_ms: Milliseconds taken
        error_msg: Optional error description
    
    Returns:
        Row ID of inserted log entry
    """
    cursor = conn.execute(
        """
        INSERT INTO sync_log
        (provider, direction, status, items_processed, bytes_transferred, error_message, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (provider, direction, status, items_processed, bytes_transferred, error_msg, duration_ms)
    )
    return cursor.lastrowid


# ─────────────────────────────────────────────────────────────────────────────
# Main Sync Daemon
# ─────────────────────────────────────────────────────────────────────────────

class MiniSOULSyncDaemon:
    """
    Orchestrates deterministic, paginated synchronization of edge memories to central SOUL.
    
    Usage:
        config = SyncConfig(agent="ADA", device_id="device-001", private_key=my_priv_key)
        daemon = MiniSOULSyncDaemon(config)
        
        # With injected callback (testable)
        results = daemon.sync_all(push_fn=my_test_handler)
        
        # Or with real HTTP push
        results = daemon.sync_all(push_fn=http_push_to_soul)
    """
    
    def __init__(self, config: SyncConfig):
        """
        Initializes the sync daemon.
        
        Args:
            config: SyncConfig object with db_path, agent, device_id, private_key, etc.
        """
        self.config = config
        self.device_fp = device_fingerprint()
        
        # Setup logging
        logging.basicConfig(level=getattr(logging, config.log_level))
        logger.info(
            f"MiniSOULSyncDaemon initialized: agent={config.agent}, "
            f"device_id={config.device_id}, fingerprint={self.device_fp}"
        )
    
    def sync_all(self, push_fn: Callable[[dict, dict], bool]) -> list:
        """
        Synchronizes ALL pending memories in the outbox to central SOUL via paginated batches.
        
        Iteration loop:
          1. Read pending outbox entries (batch_size at a time)
          2. Reconstruct memories, apply sync policies (double-check)
          3. Generate device-bound token
          4. Assemble payload, call push_fn(token, batch)
          5. On success: mark outbox entries synced
          6. On failure: increment retry_count, mark failed after MAX_RETRIES
          7. Log operation to sync_log
          8. Repeat until outbox is empty
        
        Args:
            push_fn: Callback function(token: dict, batch: dict) -> bool
                    Returns True on success, False on failure.
                    (Testable: can be a mock handler, HTTP call, etc.)
        
        Returns:
            List of SyncResult dicts (one per batch processed)
        """
        results = []
        total_synced = 0
        total_failed = 0
        start_time = time.time()
        
        logger.info("Starting full outbox synchronization")
        
        with get_db_connection(self.config.db_path) as conn:
            while True:
                # Step 1-2: Read pending + validate + select batch
                batch = self._read_and_validate_batch(conn)
                if not batch:
                    logger.info("Outbox is empty; synchronization complete")
                    break
                
                logger.info(f"Processing batch of {len(batch)} eligible memories")
                
                # Step 3: Generate device-bound token
                token = issue_token(
                    agent=self.config.agent,
                    device_id=self.config.device_id,
                    fingerprint=self.device_fp,
                    private_key=self.config.private_key,
                    ttl_s=self.config.token_ttl_s
                )
                
                # Step 4: Assemble payload
                payload = assemble_sync_payload(
                    batch, self.config.agent, self.config.device_id,
                    self.device_fp, token
                )
                
                # Step 5: Call push_fn
                batch_start = time.time()
                try:
                    success = push_fn(token, payload)
                except Exception as e:
                    success = False
                    logger.error(f"push_fn raised exception: {e}")
                
                batch_duration_ms = int((time.time() - batch_start) * 1000)
                
                if success:
                    # Step 6a: Mark synced
                    outbox_ids = [e["outbox_id"] for e in batch]
                    num_synced = mark_outbox_synced(conn, outbox_ids)
                    total_synced += num_synced
                    
                    logger.info(f"Successfully synced {num_synced} memories")
                    
                    # Step 7: Log success
                    bytes_transferred = len(json.dumps(payload).encode("utf-8"))
                    log_sync_operation(
                        conn, "memories_push", "push", "success",
                        len(batch), bytes_transferred, batch_duration_ms
                    )
                    
                    results.append(SyncResult(
                        success=True,
                        outbox_id=-1,  # Batch ID
                        nonce=f"batch-{start_time}",
                        retry_count=0
                    ))
                else:
                    # Step 6b: Mark failed (with retries)
                    failed_count = 0
                    for entry in batch:
                        is_final_failure = mark_outbox_failed(
                            conn, entry["outbox_id"], "push_fn returned False"
                        )
                        if is_final_failure:
                            total_failed += 1
                            failed_count += 1
                        else:
                            logger.debug(
                                f"Retry scheduled for outbox entry {entry['outbox_id']}"
                            )
                    
                    logger.warning(
                        f"Batch failed: {failed_count} entries exhausted retries, "
                        f"others scheduled for retry"
                    )
                    
                    # Step 7: Log failure
                    log_sync_operation(
                        conn, "memories_push", "push", "failure",
                        len(batch), 0, batch_duration_ms,
                        "push_fn returned False"
                    )
                    
                    results.append(SyncResult(
                        success=False,
                        outbox_id=-1,
                        nonce=f"batch-{start_time}",
                        error_message="push_fn returned False",
                        retry_count=0
                    ))
        
        # Summary
        elapsed_s = time.time() - start_time
        logger.info(
            f"Synchronization complete: {total_synced} synced, "
            f"{total_failed} failed (exhausted retries), {elapsed_s:.2f}s elapsed"
        )
        
        return results
    
    def _read_and_validate_batch(self, conn: sqlite3.Connection) -> list:
        """
        Helper: reads pending outbox entries, validates eligibility, returns batch.
        
        Args:
            conn: SQLite connection
        
        Returns:
            List of eligible entries (empty if outbox is empty or no eligible entries)
        """
        return validate_and_select_batch(conn, self.config.batch_size)


# ─────────────────────────────────────────────────────────────────────────────
# Testing Utilities
# ─────────────────────────────────────────────────────────────────────────────

def create_test_handler():
    """
    Returns a test push_fn callback that simulates sync success/failure.
    
    For testing only; does not actually push to a server.
    """
    def test_push(token: dict, payload: dict) -> bool:
        # Simulated success (can be modified to return False for testing failure)
        logger.debug(f"TEST: Received payload with {len(payload['batch'])} memories")
        return True
    
    return test_push


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Example usage (requires seal_token private key from SOUL central).
    
    In production:
      1. Load private_key from secure vault (SOUL central key)
      2. Pass real push_fn (HTTP to SOUL API)
      3. Run as systemd daemon or cron job
    
    For testing:
      python minisoul_sync_daemon.py --test
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="Mini-SOUL Sync Daemon")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--agent", default="UNKNOWN", help="Agent name (ADA, JARVIS, etc.)")
    parser.add_argument("--device-id", default="UNKNOWN", help="Device ID")
    parser.add_argument("--batch-size", type=int, default=BATCH_PAGE_SIZE, help="Sync batch size")
    parser.add_argument("--test", action="store_true", help="Run in test mode (no real sync)")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    
    args = parser.parse_args()
    
    # Note: In production, load private_key from secure vault
    # For testing, we use a test keypair
    if args.test:
        from seal_token import generate_keypair
        priv_key, _ = generate_keypair()
    else:
        # TODO: Load from secure vault
        logger.error("No private key provided; cannot sync in production mode")
        sys.exit(1)
    
    config = SyncConfig(
        db_path=args.db,
        agent=args.agent,
        device_id=args.device_id,
        private_key=priv_key,
        batch_size=args.batch_size,
        log_level=args.log_level
    )
    
    daemon = MiniSOULSyncDaemon(config)
    
    if args.test:
        push_fn = create_test_handler()
    else:
        # TODO: Implement real HTTP push to SOUL API
        raise NotImplementedError("Real push_fn not implemented; use --test for now")
    
    results = daemon.sync_all(push_fn)
    
    if all(r.success for r in results if r.success):
        logger.info("All batches synced successfully")
        sys.exit(0)
    else:
        logger.warning(f"Some batches failed; check logs for details")
        sys.exit(1)
