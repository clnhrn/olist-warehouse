from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agent.config import AgentSettings, Target
from agent.llm import SqlAgent, Usage
from agent.query_log import LOG_COLUMNS, log_row
from agent.schema import load_schema, render_schema
from agent.warehouse import QueryResult, WarehouseError


def tool_use(name, tool_input, block_id="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def response(*content, stop_reason="tool_use"):
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=1000,
    )
    return SimpleNamespace(content=list(content), stop_reason=stop_reason, usage=usage)


def answer_call(status="answered", chart=None, explanation="There are 3 orders."):
    chart = chart or {"type": "none", "x": None, "y": None, "series": None, "title": None}
    return tool_use("answer", {"status": status, "explanation": explanation, "chart": chart}, "ta")


class FakeMessages:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        # Copy, because the agent appends to the same list later.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


class FakeWarehouse:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.queries = []

    def run(self, sql):
        self.queries.append(sql)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


RESULT = QueryResult(columns=("orders",), rows=((3,),), bytes_billed=1024)
SETTINGS = AgentSettings(target=Target.LOCAL, max_attempts=3)


@pytest.fixture(scope="module")
def schema():
    return load_schema()


def make_agent(schema, messages, warehouse, settings=SETTINGS):
    return SqlAgent(settings, schema, warehouse, messages)


def test_runs_sql_then_answers(schema):
    messages = FakeMessages(
        response(tool_use("run_sql", {"sql": "SELECT COUNT(*) AS orders FROM gold.fact_orders"})),
        response(answer_call()),
    )
    warehouse = FakeWarehouse(RESULT)
    answer = make_agent(schema, messages, warehouse).ask("How many orders?")

    assert answer.status == "answered"
    assert answer.explanation == "There are 3 orders."
    assert answer.result == RESULT
    assert warehouse.queries == ["SELECT COUNT(*) AS orders FROM dev_gold.fact_orders LIMIT 1000"]
    assert answer.sql == warehouse.queries[0]
    assert answer.chart is None
    assert answer.usage.calls == 2


def test_feeds_errors_back_and_retries(schema):
    messages = FakeMessages(
        response(tool_use("run_sql", {"sql": "SELECT nope FROM gold.fact_orders"})),
        response(tool_use("run_sql", {"sql": "SELECT COUNT(*) AS orders FROM gold.fact_orders"})),
        response(answer_call()),
    )
    warehouse = FakeWarehouse(WarehouseError("column nope not found"), RESULT)
    answer = make_agent(schema, messages, warehouse).ask("How many orders?")

    assert answer.status == "answered"
    assert [a.error for a in answer.attempts] == ["column nope not found", None]
    # The second request carries the error back as a tool_result.
    tool_result = messages.requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "column nope not found" in tool_result["content"]


def test_guard_refusal_never_reaches_the_warehouse(schema):
    messages = FakeMessages(
        response(tool_use("run_sql", {"sql": "DROP TABLE gold.fact_orders"})),
        response(answer_call(status="refused")),
    )
    warehouse = FakeWarehouse()
    answer = make_agent(schema, messages, warehouse).ask("Delete all orders")

    assert warehouse.queries == []
    assert answer.status == "refused"
    assert "Only SELECT" in answer.attempts[0].error


def test_stops_running_sql_after_max_attempts(schema):
    bad = response(tool_use("run_sql", {"sql": "SELECT x FROM gold.fact_orders"}))
    messages = FakeMessages(bad, bad, bad, bad, response(answer_call()))
    warehouse = FakeWarehouse(*[WarehouseError("bad")] * 3)
    answer = make_agent(schema, messages, warehouse).ask("?")

    assert len(warehouse.queries) == 3
    assert len(answer.attempts) == 3
    # Claude claimed an answer without any successful query: that is a failure.
    assert answer.status == "failed"


def test_gives_up_when_the_model_never_answers(schema):
    loop = response(tool_use("run_sql", {"sql": "SELECT 1 AS a FROM gold.fact_orders"}))
    agent = make_agent(schema, FakeMessages(), FakeWarehouse(*[RESULT] * 3))
    agent.messages = FakeMessages(*[loop] * agent.max_model_calls)
    answer = agent.ask("?")

    assert answer.status == "failed"
    assert "Ran out of model calls" in answer.error
    assert len(agent.messages.requests) == agent.max_model_calls


def test_model_refusal_fails_cleanly(schema):
    messages = FakeMessages(response(stop_reason="refusal"))
    answer = make_agent(schema, messages, FakeWarehouse()).ask("?")
    assert answer.status == "failed"
    assert "declined" in answer.error


def test_keeps_the_chart_spec(schema):
    chart = {"type": "line", "x": "month", "y": "revenue", "series": None, "title": "Revenue"}
    messages = FakeMessages(
        response(tool_use("run_sql", {"sql": "SELECT 1 AS month FROM gold.fact_orders"})),
        response(answer_call(chart=chart)),
    )
    answer = make_agent(schema, messages, FakeWarehouse(RESULT)).ask("Chart revenue")
    assert answer.chart == chart


def test_system_prompt_is_identical_across_questions(schema):
    first = make_agent(schema, FakeMessages(), FakeWarehouse())
    second = make_agent(schema, FakeMessages(), FakeWarehouse())
    assert first.system == second.system
    assert first.system[0]["cache_control"] == {"type": "ephemeral"}


def test_history_becomes_earlier_turns(schema):
    messages = FakeMessages(response(answer_call(status="refused")))
    agent = make_agent(schema, messages, FakeWarehouse())
    agent.ask("And in 2018?", history=[("Orders in 2017?", "45,101.")])
    sent = messages.requests[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[-1]["content"] == "And in 2018?"


def test_schema_covers_every_gold_table_and_metric(schema):
    assert schema.table_names == {
        "dim_customer",
        "dim_date",
        "dim_location",
        "dim_product",
        "dim_seller",
        "fact_order_items",
        "fact_orders",
        "fact_payments",
        "fact_reviews",
    }
    text = render_schema(schema)
    assert "## gold.fact_orders" in text
    assert "- revenue (Revenue)" in text
    assert "order_status NOT IN ('canceled', 'unavailable')" in text


def test_usage_cost_uses_list_prices():
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=1_000_000)
    # 1M in at $2 + 0.1M out at $10 + 1M cache reads at $0.20
    assert usage.cost_usd("claude-sonnet-5-5") == pytest.approx(2.0 + 1.0 + 0.2)
    assert usage.cost_usd("unknown-model") is None


def test_log_row_matches_the_table_schema(schema):
    messages = FakeMessages(
        response(tool_use("run_sql", {"sql": "SELECT COUNT(*) AS orders FROM gold.fact_orders"})),
        response(answer_call()),
    )
    answer = make_agent(schema, messages, FakeWarehouse(RESULT)).ask("How many orders?")
    row = log_row(answer, Target.DEV, "s1", datetime(2026, 10, 13, tzinfo=UTC))

    assert list(row) == list(LOG_COLUMNS)
    assert row["status"] == "answered"
    assert row["bytes_billed"] == 1024
    assert row["rows_returned"] == 1
    assert row["attempts"] == 1
    assert row["input_tokens"] == 2 * (100 + 1000)
    assert row["asked_at"] == "2026-10-13T00:00:00+00:00"


def test_bigquery_errors_drop_the_url_and_project():
    from agent.warehouse import BigQueryWarehouse

    error = SimpleNamespace(errors=[{"message": "Access Denied: Table my-proj:gold.x"}])
    fake_self = SimpleNamespace(settings=AgentSettings(target=Target.DEV, project_id="my-proj"))
    message = BigQueryWarehouse._error_message(fake_self, error)
    assert message == "Access Denied: Table <project>:gold.x"


COUNT_SQL = {"sql": "SELECT COUNT(*) AS orders FROM gold.fact_orders"}
BIG = QueryResult(columns=("orders", "late_rate"), rows=((99441, 0.0677309),))


def test_placeholders_are_filled_with_exact_values(schema):
    text = "There are {{r1.orders:int}} orders and {{r1.late_rate:pct}} arrived late."
    messages = FakeMessages(
        response(tool_use("run_sql", COUNT_SQL)), response(answer_call(explanation=text))
    )
    answer = make_agent(schema, messages, FakeWarehouse(BIG)).ask("How many orders?")

    assert answer.status == "answered"
    assert answer.explanation == "There are 99,441 orders and 6.77% arrived late."
    assert answer.explanation_template == text


def test_written_out_numbers_are_sent_back_for_a_fix(schema):
    messages = FakeMessages(
        response(tool_use("run_sql", COUNT_SQL)),
        response(answer_call(explanation="There are about 99,400 orders.")),
        response(answer_call(explanation="There are {{r1.orders:int}} orders.")),
    )
    answer = make_agent(schema, messages, FakeWarehouse(BIG)).ask("How many orders?")

    assert answer.status == "answered"
    assert answer.explanation == "There are 99,441 orders."
    assert answer.answer_rejections == 1
    rejection = messages.requests[2]["messages"][-1]["content"][0]
    assert rejection["is_error"] is True
    assert "99,400" in rejection["content"]


def test_bad_placeholders_fail_after_the_rejection_limit(schema):
    bad = response(answer_call(explanation="There are {{r9.orders:int}} orders."))
    messages = FakeMessages(response(tool_use("run_sql", COUNT_SQL)), bad, bad, bad)
    answer = make_agent(schema, messages, FakeWarehouse(BIG)).ask("How many orders?")

    assert answer.status == "failed"
    assert "row 9 does not exist" in answer.error
    assert answer.answer_rejections == 2
    assert len(messages.requests) == 4


def test_text_without_the_answer_tool_gets_one_reminder(schema):
    text = response(
        SimpleNamespace(type="text", text="About 99,441 orders."), stop_reason="end_turn"
    )
    messages = FakeMessages(
        response(tool_use("run_sql", COUNT_SQL)),
        text,
        response(answer_call(explanation="There are {{r1.orders:int}} orders.")),
    )
    answer = make_agent(schema, messages, FakeWarehouse(BIG)).ask("How many orders?")

    assert answer.status == "answered"
    assert answer.explanation == "There are 99,441 orders."
    assert "answer tool" in messages.requests[2]["messages"][-1]["content"]


def test_text_twice_fails_instead_of_showing_unchecked_numbers(schema):
    text = response(
        SimpleNamespace(type="text", text="About 99,441 orders."), stop_reason="end_turn"
    )
    messages = FakeMessages(response(tool_use("run_sql", COUNT_SQL)), text, text)
    answer = make_agent(schema, messages, FakeWarehouse(BIG)).ask("How many orders?")

    assert answer.status == "failed"
    assert "instead of with the answer tool" in answer.error
