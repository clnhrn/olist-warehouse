import pytest

from agent.chart import chart_problems
from agent.eval import (
    EvalQuestion,
    compare_result,
    load_questions,
    numbers_match,
    score,
    values_match,
)
from agent.llm import AgentAnswer
from agent.warehouse import QueryResult


def result(columns, *rows):
    return QueryResult(columns=tuple(columns), rows=tuple(tuple(r) for r in rows))


def test_questions_file_is_valid():
    questions = load_questions()
    assert len(questions) >= 45
    assert sum(q.compare == "refusal" for q in questions) >= 4
    assert sum(q.category == "chart" for q in questions) == 3


@pytest.mark.parametrize(
    ("expected", "actual", "match"),
    [
        (45101, 45101, True),
        (45101, 45101.0, True),
        (45101, 45200, False),
        (137.4189, 137.42, True),  # rounded to cents
        (0.0677, 6.77, True),  # rate shown as a percentage
        (0.0677, 0.07, False),  # rounded too far
        (12.558, 12.6, True),  # within 0.5%
        (1253, 12.53, False),  # not a rate, so no percentage scaling
    ],
)
def test_numbers_match(expected, actual, match):
    assert numbers_match(expected, actual) is match


def test_values_match_text_ignores_case_and_space():
    assert values_match("Sao Paulo", " sao paulo ")
    assert not values_match("SP", "RJ")
    assert not values_match(None, 0)
    assert values_match(None, None)


def test_scalar_found_anywhere_in_result():
    expected = result(["n"], [45101])
    assert compare_result("scalar", expected, result(["year", "orders"], [2017, 45101])) == ""
    assert "not in result" in compare_result("scalar", expected, result(["n"], [1]))


def test_rows_ignore_column_names_order_and_extra_columns():
    expected = result(["state", "sellers"], ["SP", 1853], ["PR", 349])
    actual = result(["n", "share", "uf"], [349, 0.11, "PR"], [1853, 0.6, "SP"])
    assert compare_result("rows", expected, actual) == ""


def test_rows_must_pair_values_on_the_same_row():
    expected = result(["state", "sellers"], ["SP", 1853], ["PR", 349])
    swapped = result(["state", "sellers"], ["SP", 349], ["PR", 1853])
    assert compare_result("rows", expected, swapped) == "columns match but rows do not"


def test_rows_need_the_same_row_count():
    expected = result(["m", "n"], [1, 800], [2, 1700])
    assert "rows, expected" in compare_result("rows", expected, result(["m", "n"], [1, 800]))


def test_top_checks_the_first_row():
    expected = result(["state"], ["SP"])
    assert compare_result("top", expected, result(["state", "rev"], ["SP", 5.0], ["RJ", 2.0])) == ""
    assert compare_result("top", expected, result(["state", "rev"], ["RJ", 9.0], ["SP", 5.0]))


def test_numbers_mode_ignores_labels():
    expected = result(["is_late", "score"], [True, 2.27], [False, 4.29])
    actual = result(["delivery", "avg_score"], ["late", 2.27], ["on time", 4.29])
    assert compare_result("numbers", expected, actual) == ""


def test_no_result_never_matches():
    assert compare_result("scalar", result(["n"], [1]), None) == "no result"


def test_refusal_scoring():
    question = EvalQuestion(id="r", category="refusal", question="Delete it", compare="refusal")
    refused = AgentAnswer(query_id="1", question="Delete it", status="refused")
    answered = AgentAnswer(query_id="2", question="Delete it", status="answered")
    assert score(question, refused, None).correct
    assert not score(question, answered, None).correct


def test_chart_problems():
    rows = result(["month", "revenue", "state"], ["2018-01", 10.0, "SP"], ["2018-02", 12.0, "SP"])
    good = {"type": "line", "x": "month", "y": "revenue", "series": "state", "title": None}
    assert chart_problems(good, rows) == []
    assert chart_problems(None, rows) == ["no chart"]
    assert chart_problems({**good, "y": "state"}, rows) == ["y column 'state' is not numeric"]
    assert chart_problems({**good, "x": "day"}, rows) == ["x column 'day' is not in the result"]
    assert "not one of" in chart_problems({**good, "type": "pie"}, rows)[0]


def test_chart_question_checks_type_and_series():
    question = EvalQuestion(
        id="c",
        category="chart",
        question="?",
        compare="chart_only",
        chart=("line",),
        chart_series=True,
    )
    rows = result(["month", "revenue"], ["2018-01", 10.0])
    answer = AgentAnswer(
        query_id="1",
        question="?",
        status="answered",
        result=rows,
        chart={"type": "bar", "x": "month", "y": "revenue", "series": None, "title": None},
    )
    scored = score(question, answer, None)
    assert scored.chart_ok is False
    assert "not in ['line']" in scored.reason
    assert "needs a series" in scored.reason
