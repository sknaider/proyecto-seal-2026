"""
SEAL Phase-1 Device-Side Revocation Client
────────────────────────────────────────────────────────────────────────────
Module: seal_revocation_client
Purpose: Device-side revocation cache with TTL-based re-sync and pluggable source
Status: Phase-1 Security implementation (per FASE1_SECURITY_AUTH_GATE_NEXUS.md §5)

Architecture:
  • RevocationCache stores revoked JTIs with 15-min TTL
  • is_revoked(jti) → bool using local cache
  • refresh(source_callable) → pluggable refresh from any source (not coupled to endpoint)
  • Fail-closed: if cache expires & can't refresh for >max_stale (7 days), mark suspicious

Design Rationale:
  1. Device-side caching minimizes network round-trips
  2. TTL (15 min) bounds token exposure in theft scenario
  3. Pluggable source_callable allows later integration with actual SOUL endpoint
     without changing RevocationCache interface
  4. Fail-closed on stale cache ensures paranoia: if we can't sync, better to reject
     than trust possibly outdated cache
  5. No external deps (uses only stdlib datetime, threading, logging)

Self-tests:
  • Revoked JTI → is_revoked returns True
  • Non-revoked JTI → is_revoked returns False
  • Cache TTL expiry → triggers refresh
  • Refresh failure + max_stale exceeded → marks cache suspicious & rejects
  • Concurrent access → thread-safe via lock
"""

import json
import time
import threading
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Set, Tuple, Dict
import traceback

# ─────────────────────────────────────────────────────────────────────────
# Configuration & Constants
# ─────────────────────────────────────────────────────────────────────────

CACHE_TTL_SECONDS = 15 * 60  # 15 minutes
MAX_STALE_SECONDS = 7 * 24 * 60 * 60  # 7 days (fail-closed threshold)
SYNC_INTERVAL_SECONDS = 60  # Re-sync every 60s in background

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# RevocationCache: Main Implementation
# ─────────────────────────────────────────────────────────────────────────

class RevocationCache:
    """
    Device-side revocation cache with TTL and pluggable refresh source.

    Attributes:
        agent (str): Agent identifier (e.g., 'ADA', 'JARVIS')
        device_id (str): Device identifier (e.g., 'device-abc123def456')
        revoked_jtis (Set[str]): Set of revoked token JTI identifiers
        cache_expiry_time (float): Unix timestamp when cache expires
        cache_ttl_seconds (int): TTL for cache (default 15 min)
        last_sync_time (float): Unix timestamp of last successful sync
        last_sync_error (Optional[str]): Last sync error message
        suspicious_mode (bool): True if cache stale >max_stale (paranoia mode)
        source_callable (Optional[Callable]): Pluggable function that returns Set[str] of revoked JTIs
    """

    def __init__(
        self,
        agent: str,
        device_id: str,
        cache_ttl_seconds: int = CACHE_TTL_SECONDS,
        max_stale_seconds: int = MAX_STALE_SECONDS,
    ):
        """
        Initialize RevocationCache.

        Args:
            agent: Agent identifier (ADA, JARVIS, ALICE, NEXUS, DUM)
            device_id: Device identifier (device-XXXXXXXXXXXXXX)
            cache_ttl_seconds: How long cache is valid before requiring refresh
            max_stale_seconds: Maximum staleness before paranoia mode
        """
        self.agent = agent
        self.device_id = device_id
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_stale_seconds = max_stale_seconds

        # Cache state
        self.revoked_jtis: Set[str] = set()
        self.cache_expiry_time: float = 0.0
        self.last_sync_time: float = 0.0
        self.last_sync_error: Optional[str] = None
        self.suspicious_mode: bool = False

        # Pluggable source
        self.source_callable: Optional[Callable[[], Set[str]]] = None
        # Hardening (red-team NEXUS 2026-07-02): la fuente se fija UNA vez en bootstrap y se BLOQUEA.
        # Evita que codigo untrusted post-bootstrap re-setee la fuente a una maliciosa (ej: que devuelva
        # set() vacio = bypass de revocacion). set_source levanta si ya esta bloqueada.
        self._source_locked: bool = False

        # Thread safety
        self.lock = threading.Lock()

        # Background sync daemon
        self._sync_thread: Optional[threading.Thread] = None
        self._stop_sync = False

        logger.info(
            f"[RevocationCache] initialized: agent={agent}, device={device_id}, "
            f"ttl={cache_ttl_seconds}s, max_stale={max_stale_seconds}s"
        )

    def set_source(self, source_callable: Callable[[], Set[str]]) -> None:
        """
        Set the pluggable source for refreshing revoked JTIs.

        Args:
            source_callable: Function that returns Set[str] of revoked JTIs.
                           Can raise Exception if sync fails.
        """
        with self.lock:
            if self._source_locked:
                raise RuntimeError(
                    "set_source ya fue fijado en bootstrap y esta BLOQUEADO (anti-inyeccion de fuente maliciosa)"
                )
            self.source_callable = source_callable
            self._source_locked = True
            logger.info(f"[RevocationCache] source set + LOCKED: {source_callable.__name__}")

    def is_revoked(self, jti: str) -> Tuple[bool, str]:
        """
        Check if a JWT is revoked using local cache.

        Args:
            jti: JWT ID (typically the nonce/token_id from the JWT)

        Returns:
            Tuple[bool, str]: (is_revoked, reason)
                - is_revoked=True, reason='in_revoked_list' if JTI is revoked
                - is_revoked=True, reason='cache_expired' if cache expired
                - is_revoked=True, reason='cache_stale' if cache stale >max_stale
                - is_revoked=False, reason='not_revoked' if JTI not in revoked list
        """
        with self.lock:
            now = time.time()

            # Paranoia check: cache critically stale
            if self.suspicious_mode:
                logger.warning(
                    f"[RevocationCache] SUSPICIOUS MODE active for {self.agent}/"
                    f"{self.device_id} (cache stale >{self.max_stale_seconds}s)"
                )
                return True, "cache_stale"

            # Check if cache has expired
            if now > self.cache_expiry_time:
                logger.warning(
                    f"[RevocationCache] Cache expired for {self.agent}/{self.device_id} "
                    f"(expired at {self.cache_expiry_time}, now {now})"
                )
                return True, "cache_expired"

            # Check if JTI is in revoked set
            if jti in self.revoked_jtis:
                reason = self.last_sync_error or "in_revoked_list"
                logger.warning(f"[RevocationCache] JTI revoked: {jti} (reason: {reason})")
                return True, reason

            logger.debug(f"[RevocationCache] JTI not revoked: {jti}")
            return False, "not_revoked"

    def refresh(self) -> Tuple[bool, str]:
        """
        Refresh revocation cache from pluggable source.

        Returns:
            Tuple[bool, str]: (success, message)
                - (True, "refreshed") if successful
                - (False, error_msg) if refresh failed

        Behavior:
            • Calls source_callable() to get Set[str] of revoked JTIs
            • Updates cache_expiry_time to now + cache_ttl_seconds
            • If source_callable not set, returns (False, "source_not_set")
            • If source raises Exception, logs error and sets last_sync_error
            • After max_stale exceeded with no successful sync, sets suspicious_mode
        """
        with self.lock:
            now = time.time()

            # Guard: source must be set
            if self.source_callable is None:
                msg = "source_callable not set"
                logger.error(f"[RevocationCache] Cannot refresh: {msg}")
                return False, msg

            # Try to refresh
            try:
                revoked_set = self.source_callable()
                if not isinstance(revoked_set, set):
                    raise TypeError(f"source must return Set[str], got {type(revoked_set)}")

                self.revoked_jtis = revoked_set
                self.cache_expiry_time = now + self.cache_ttl_seconds
                self.last_sync_time = now
                self.last_sync_error = None
                self.suspicious_mode = False

                logger.info(
                    f"[RevocationCache] Refreshed: {len(revoked_set)} revoked JTIs, "
                    f"expires at {self.cache_expiry_time}"
                )
                return True, "refreshed"

            except Exception as e:
                self.last_sync_error = str(e)
                logger.error(
                    f"[RevocationCache] Refresh failed: {e}\n{traceback.format_exc()}"
                )

                # Check if cache is now stale beyond max_stale
                age_seconds = now - self.last_sync_time
                if age_seconds > self.max_stale_seconds:
                    self.suspicious_mode = True
                    logger.critical(
                        f"[RevocationCache] PARANOIA MODE: cache stale for >{self.max_stale_seconds}s. "
                        f"Setting suspicious_mode=True (all tokens will be rejected)"
                    )

                return False, f"refresh failed: {e}"

    def start_background_sync(self, interval_seconds: int = SYNC_INTERVAL_SECONDS) -> None:
        """
        Start background thread that periodically refreshes cache.

        Args:
            interval_seconds: How often to call refresh() in background
        """
        with self.lock:
            if self._sync_thread is not None and self._sync_thread.is_alive():
                logger.warning("[RevocationCache] Background sync already running")
                return

            self._stop_sync = False

        self._sync_thread = threading.Thread(
            target=self._background_sync_loop,
            args=(interval_seconds,),
            daemon=True,
            name=f"revocation-sync-{self.agent}-{self.device_id}",
        )
        self._sync_thread.start()
        logger.info(
            f"[RevocationCache] Background sync started (interval={interval_seconds}s)"
        )

    def stop_background_sync(self) -> None:
        """Stop background sync thread."""
        with self.lock:
            self._stop_sync = True

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5)
            logger.info("[RevocationCache] Background sync stopped")

    def _background_sync_loop(self, interval_seconds: int) -> None:
        """Background daemon that calls refresh() periodically."""
        logger.info(
            f"[RevocationCache] Sync daemon started for {self.agent}/{self.device_id}"
        )

        while True:
            if self._stop_sync:
                break

            time.sleep(interval_seconds)

            if self._stop_sync:
                break

            success, msg = self.refresh()
            if success:
                logger.debug(f"[RevocationCache] Background sync OK: {msg}")
            else:
                logger.warning(f"[RevocationCache] Background sync failed: {msg}")

        logger.info(
            f"[RevocationCache] Sync daemon stopped for {self.agent}/{self.device_id}"
        )

    def get_stats(self) -> Dict:
        """
        Get current cache statistics.

        Returns:
            Dict with cache state (for logging/monitoring)
        """
        with self.lock:
            now = time.time()
            age_seconds = now - self.last_sync_time if self.last_sync_time > 0 else -1
            remaining_ttl = self.cache_expiry_time - now

            return {
                "agent": self.agent,
                "device_id": self.device_id,
                "revoked_count": len(self.revoked_jtis),
                "cache_expired": now > self.cache_expiry_time,
                "cache_remaining_ttl_seconds": max(0, remaining_ttl),
                "last_sync_time": self.last_sync_time,
                "cache_age_seconds": age_seconds,
                "last_sync_error": self.last_sync_error,
                "suspicious_mode": self.suspicious_mode,
            }


# ─────────────────────────────────────────────────────────────────────────
# Self-Tests
# ─────────────────────────────────────────────────────────────────────────

def test_basic_revoked_check():
    """Test 1: Revoked JTI returns True"""
    print("\n[TEST 1] Basic revoked check")
    cache = RevocationCache("ADA", "device-test001", cache_ttl_seconds=10)

    # Set up mock source
    def mock_source():
        return {"jti-revoked-001", "jti-revoked-002"}

    cache.set_source(mock_source)
    cache.refresh()

    # Check revoked
    is_revoked, reason = cache.is_revoked("jti-revoked-001")
    assert is_revoked is True, f"Expected True, got {is_revoked}"
    assert reason == "in_revoked_list", f"Expected 'in_revoked_list', got {reason}"
    print("  ✓ Revoked JTI correctly identified")


def test_non_revoked_check():
    """Test 2: Non-revoked JTI returns False"""
    print("\n[TEST 2] Non-revoked check")
    cache = RevocationCache("ADA", "device-test002", cache_ttl_seconds=10)

    def mock_source():
        return {"jti-revoked-001"}

    cache.set_source(mock_source)
    cache.refresh()

    is_revoked, reason = cache.is_revoked("jti-clean-001")
    assert is_revoked is False, f"Expected False, got {is_revoked}"
    assert reason == "not_revoked", f"Expected 'not_revoked', got {reason}"
    print("  ✓ Non-revoked JTI correctly identified")


def test_cache_ttl_expiry():
    """Test 3: Cache TTL expiry triggers reject"""
    print("\n[TEST 3] Cache TTL expiry")
    cache = RevocationCache("ADA", "device-test003", cache_ttl_seconds=1)

    def mock_source():
        return {"jti-revoked-001"}

    cache.set_source(mock_source)
    cache.refresh()

    # Verify fresh cache works
    is_revoked, _ = cache.is_revoked("jti-clean-001")
    assert is_revoked is False, "Should not be revoked with fresh cache"
    print("  ✓ Fresh cache: JTI not revoked")

    # Wait for TTL to expire
    print("  (waiting 2 seconds for TTL to expire...)")
    time.sleep(2)

    # Now check after expiry
    is_revoked, reason = cache.is_revoked("jti-clean-001")
    assert is_revoked is True, f"Expected True after TTL expiry, got {is_revoked}"
    assert reason == "cache_expired", f"Expected 'cache_expired', got {reason}"
    print("  ✓ Expired cache: correctly rejects all tokens")


def test_refresh_failure_with_max_stale():
    """Test 4: Refresh failure + max_stale exceeded → paranoia mode"""
    print("\n[TEST 4] Refresh failure + max_stale → suspicious mode")
    cache = RevocationCache(
        "ADA",
        "device-test004",
        cache_ttl_seconds=1,
        max_stale_seconds=2,  # Short max_stale for testing
    )

    call_count = 0

    def mock_source_fail_after_first():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"jti-revoked-001"}
        else:
            raise Exception("Source temporarily unavailable")

    cache.set_source(mock_source_fail_after_first)

    # First refresh succeeds
    success, msg = cache.refresh()
    assert success is True, f"First refresh should succeed, got {msg}"
    print("  ✓ First refresh succeeded")

    # Wait for TTL to expire and max_stale to approach
    print("  (waiting 3 seconds for TTL expiry + max_stale threshold...)")
    time.sleep(3)

    # Try to refresh again (will fail)
    success, msg = cache.refresh()
    assert success is False, f"Second refresh should fail"
    print("  ✓ Second refresh failed as expected")

    # Check cache state
    stats = cache.get_stats()
    assert stats["suspicious_mode"] is True, "Should enter suspicious mode"
    print("  ✓ Entered suspicious mode (paranoia)")

    # Now any token check should be rejected
    is_revoked, reason = cache.is_revoked("jti-any-token")
    assert is_revoked is True, f"Should reject all tokens in suspicious mode"
    assert reason == "cache_stale", f"Expected 'cache_stale', got {reason}"
    print("  ✓ All tokens rejected in suspicious mode")


def test_background_sync():
    """Test 5: Background sync thread works"""
    print("\n[TEST 5] Background sync thread")
    cache = RevocationCache("ADA", "device-test005", cache_ttl_seconds=10)

    sync_count = 0

    def mock_source():
        nonlocal sync_count
        sync_count += 1
        return {"jti-revoked-001"}

    cache.set_source(mock_source)

    # Start background sync with 1-second interval
    cache.start_background_sync(interval_seconds=1)
    print("  ✓ Background sync started")

    # Wait for ~2 syncs
    time.sleep(2.5)

    stats = cache.get_stats()
    print(f"  Sync count after 2.5s: {sync_count} (expected ~2-3)")
    assert sync_count >= 2, f"Should have synced at least 2x, got {sync_count}"
    print("  ✓ Background sync executed multiple times")

    cache.stop_background_sync()
    print("  ✓ Background sync stopped")


def test_concurrent_access():
    """Test 6: Thread-safe concurrent access"""
    print("\n[TEST 6] Concurrent access (thread safety)")
    cache = RevocationCache("ADA", "device-test006", cache_ttl_seconds=10)

    def mock_source():
        return {"jti-revoked-001", "jti-revoked-002"}

    cache.set_source(mock_source)
    cache.refresh()

    results = []

    def worker(jti):
        is_revoked, reason = cache.is_revoked(jti)
        results.append((jti, is_revoked, reason))

    # Spawn multiple threads checking JTIs concurrently
    threads = []
    for i in range(5):
        jti = f"jti-check-{i % 2}"  # Will check revoked and clean JTIs
        t = threading.Thread(target=worker, args=(jti,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(results) == 5, f"Expected 5 results, got {len(results)}"
    print(f"  ✓ Concurrent checks completed: {len(results)} results")
    for jti, is_revoked, reason in results:
        print(f"    - {jti}: revoked={is_revoked}, reason={reason}")


def test_source_not_set():
    """Test 7: Refresh with no source set returns error"""
    print("\n[TEST 7] Refresh without source set")
    cache = RevocationCache("ADA", "device-test007")

    # Try to refresh without setting source
    success, msg = cache.refresh()
    assert success is False, f"Should fail without source, got success={success}"
    assert "source_callable not set" in msg or "source_not_set" in msg, f"Expected source error in msg, got {msg}"
    print(f"  ✓ Correctly rejected refresh: {msg}")


def test_get_stats():
    """Test 8: Statistics reporting"""
    print("\n[TEST 8] Statistics reporting")
    cache = RevocationCache("ADA", "device-test008", cache_ttl_seconds=10)

    def mock_source():
        return {"jti-revoked-001"}

    cache.set_source(mock_source)
    cache.refresh()

    stats = cache.get_stats()
    assert "revoked_count" in stats, "Stats missing revoked_count"
    assert "cache_expired" in stats, "Stats missing cache_expired"
    assert stats["revoked_count"] == 1, f"Expected 1 revoked JTI, got {stats['revoked_count']}"
    assert stats["agent"] == "ADA", f"Expected agent ADA, got {stats['agent']}"
    print(f"  ✓ Stats retrieved: {json.dumps(stats, indent=2)}")


# ─────────────────────────────────────────────────────────────────────────
# Main: Run all self-tests
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 80)
    print("SEAL Revocation Client - Self-Test Suite")
    print("=" * 80)

    # Configure logging for tests
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    tests = [
        test_basic_revoked_check,
        test_non_revoked_check,
        test_cache_ttl_expiry,
        test_refresh_failure_with_max_stale,
        test_background_sync,
        test_concurrent_access,
        test_source_not_set,
        test_get_stats,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"\n  ✗ FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"\n  ✗ ERROR: {e}\n{traceback.format_exc()}")
            failed += 1

    print("\n" + "=" * 80)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("=" * 80)

    if failed > 0:
        exit(1)
    else:
        print("\n✓ All self-tests passed!")
