#!/usr/bin/env python3
"""Contratos semánticos del health-check de instrumentación."""

import instrumentation_health as health


def test_event_driven_tables_are_not_periodic_freshness_contracts():
    for table in ("bench_runs", "drift_events"):
        assert table in health.EVENT_DRIVEN
        assert table not in health.EXPECTED


def test_periodic_and_event_driven_contracts_do_not_overlap():
    assert set(health.EXPECTED).isdisjoint(health.EVENT_DRIVEN)
