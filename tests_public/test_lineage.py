from student_api import column_downstream, downstream_assets


def test_transitive_downstream_assets():
    graph = {
        "raw_orders": ["stg_orders"],
        "stg_orders": ["revenue"],
        "revenue": ["dashboard"],
    }
    assert downstream_assets(graph, "raw_orders") == ["stg_orders", "revenue", "dashboard"]


def test_transitive_column_downstream():
    column_graph = {
        "kb_documents.content": ["kb_active_docs.content"],
        "kb_active_docs.content": ["rag_index.embedding"],
        "rag_index.embedding": ["support_agent.answer"],
    }
    assert column_downstream(column_graph, "kb_documents.content") == [
        "kb_active_docs.content",
        "rag_index.embedding",
        "support_agent.answer",
    ]


def test_column_downstream_fan_out_is_deduplicated():
    # A column feeding two downstream columns that both feed the same final
    # column should list each downstream column once, not once per path.
    column_graph = {
        "a.col": ["b.col", "c.col"],
        "b.col": ["d.col"],
        "c.col": ["d.col"],
    }
    assert column_downstream(column_graph, "a.col") == ["b.col", "c.col", "d.col"]
