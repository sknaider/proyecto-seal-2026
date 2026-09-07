import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import soul_dual_memory_sec2_preflight as preflight


def test_proposed_policy_is_review_only_and_contains_agent_scope_gate():
    sql = preflight.proposed_agent_scope_policy_sql()

    assert "REVIEW ONLY" in sql
    assert "current_setting('app.tenant_id'" in sql
    assert "current_setting('app.agent'" in sql
    assert "COALESCE(scope, 'private') IN ('team', 'public', 'shared')" in sql


def test_proposed_multi_agent_compat_sql_matches_consolidated_model():
    sql = preflight.proposed_multi_agent_compat_sql()

    assert "Modelo Consolidado v1.1" in sql
    assert "soul_sdk_runtime" in sql
    assert "soul_sdk_user" in sql
    assert "soul_v3.user_read_audit" in sql
    assert "DROP POLICY IF EXISTS tenant_isolation_sdk_v1" in sql
    assert "CREATE POLICY memories_agent_scope_insert" in sql
    assert "CREATE POLICY memories_agent_scope_update" in sql
    assert "app.viewer" in sql
    assert "app.user_id" in sql
    assert "scope='shared' is treated as team-equivalent" in sql
    assert "AFTER SELECT" not in sql


def test_evaluate_policy_conflicts_flags_permissive_public_select_bypass():
    conflicts = preflight.evaluate_policy_conflicts(
        [
            {
                "policyname": "tenant_isolation_sdk_v1",
                "permissive": "PERMISSIVE",
                "roles": ["public"],
                "cmd": "ALL",
            },
            {
                "policyname": "strict_agent",
                "permissive": "PERMISSIVE",
                "roles": ["soul_sdk_runtime"],
                "cmd": "SELECT",
            },
        ]
    )

    assert len(conflicts) == 1
    assert conflicts[0].policy == "tenant_isolation_sdk_v1"
    assert "OR-bypass" in conflicts[0].risk


def test_migration_v1_1_applied_requires_roles_policies_and_no_conflicts():
    policies = [{"policyname": name, "roles": ["soul_sdk_runtime"], "cmd": "SELECT"} for name in preflight.REQUIRED_V1_1_POLICIES]
    roles = [preflight.RoleState(role=role, exists=True) for role in preflight.SDK_ROLES]

    assert preflight.migration_v1_1_applied(policies, roles) is True
    assert preflight.migration_v1_1_applied(
        policies + [{"policyname": "tenant_isolation_sdk_v1", "permissive": "PERMISSIVE", "roles": ["public"], "cmd": "ALL"}],
        roles,
    ) is False
    assert preflight.migration_v1_1_applied(policies[:-1], roles) is False


def test_evaluate_runtime_context_gaps_flags_tenant_without_agent():
    gaps = preflight.evaluate_runtime_context_gaps(
        {
            "runtime.py": "SELECT set_config('app.tenant_id', $1, true)",
            "bridge.py": "SELECT set_config('app.agent', $1, true)",
            "complete.py": "app.tenant_id app.agent",
        }
    )

    assert len(gaps) == 1
    assert gaps[0].file == "runtime.py"
    assert gaps[0].sets_tenant is True
    assert gaps[0].sets_agent is False


def test_evaluate_viewer_context_gaps_flags_tenant_without_viewer():
    gaps = preflight.evaluate_viewer_context_gaps(
        {
            "runtime.py": "app.tenant_id app.agent",
            "complete.py": "app.tenant_id app.agent app.viewer app.user_id",
        }
    )

    assert len(gaps) == 1
    assert gaps[0].file == "runtime.py"
    assert gaps[0].sets_agent is True


def test_build_compat_impact_counts_agent_and_user_visibility():
    impact = preflight.build_compat_impact(
        {
            "by_agent_scope_layer": [
                {"agent": "ADA", "scope": "private", "layer": "operational", "count": 10},
                {"agent": "ADA", "scope": "team", "layer": "unset", "count": 2},
                {"agent": "JARVIS", "scope": "private", "layer": "unset", "count": 20},
                {"agent": "JARVIS", "scope": "public", "layer": "unset", "count": 1},
                {"agent": "JARVIS", "scope": "shared", "layer": "unset", "count": 4},
            ],
            "private_counts": [],
            "unset_layer_counts": [],
        }
    )

    assert impact["tenant_user_visible_rows"] == 37
    assert impact["team_public_shared_rows"] == 7
    ada = next(row for row in impact["agent_view_rows"] if row["agent"] == "ADA")
    assert ada["visible_as_agent_viewer"] == 17
    assert ada["hidden_private_other_agents"] == 20
