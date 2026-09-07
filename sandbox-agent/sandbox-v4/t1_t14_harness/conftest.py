"""
SOUL v3 Test Harness — pytest config
T1-T14 validation suite (NEXUS evaluator role)
"""
import asyncio
import os
import pytest
import asyncpg
from datetime import datetime

PG_DSN_SANDBOX = os.environ.get(
    "SEAL_V3_SANDBOX_DSN",
    "postgresql://seal:REDACTADO@localhost:5433/soul_v3_sandbox"
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def db_pool():
    pool = await asyncpg.create_pool(PG_DSN_SANDBOX, min_size=1, max_size=4)
    yield pool
    await pool.close()


@pytest.fixture
async def calibration_log(db_pool):
    """Captures (predicted_confidence, actual_outcome) for Trusted Autonomy Phase 1."""
    async def log_call(test_name: str, predicted: float, actual: bool, details: dict = None):
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO test_calibration_log (test_name, predicted_confidence, actual_outcome, details, ts)
                VALUES ($1, $2, $3, $4, NOW())
            """, test_name, predicted, actual, details or {})
    yield log_call


@pytest.fixture
def seal_v3_dir():
    return "/home/dadito/IA/seal-soul-v3"
