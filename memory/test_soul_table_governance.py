from soul_table_governance import POLICIES, summarize


def test_policy_marks_core_tables_mandatory():
    assert POLICIES["agent_tasks"].obligation == "mandatory"
    assert POLICIES["memories"].obligation == "mandatory"
    assert POLICIES["agent_decisions"].obligation == "mandatory_conditional"
    assert POLICIES["soul_audit_log"].obligation == "mandatory"


def test_summarize_counts_empty_required_tables():
    data = [
        {"table": "agent_tasks", "count": 3, "policy": "mandatory"},
        {"table": "agent_decisions", "count": 0, "policy": "mandatory_conditional"},
        {"table": "scratch", "count": 0, "policy": "unclassified"},
    ]
    summary = summarize(data)
    assert summary["total_tables"] == 3
    assert summary["empty_count"] == 2
    assert summary["unclassified_count"] == 1
    assert summary["required_empty_tables"] == ["agent_decisions"]
