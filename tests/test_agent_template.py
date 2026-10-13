import pytest

from agent.template import TemplateError, format_value, render
from agent.warehouse import QueryResult

RESULT = QueryResult(
    columns=("state", "revenue", "orders", "late_rate", "avg_days"),
    rows=(("SP", 5_202_955.05, 40501, 0.0589, 8.3), ("RJ", 1_824_092.67, 12852, 0.1347, 15.2)),
)


@pytest.mark.parametrize(
    ("value", "fmt", "expected"),
    [
        (45101, "int", "45,101"),
        (45101, None, "45,101"),
        (12.558206, "num", "12.56"),
        (12.558206, None, "12.56"),
        (1003862.14, "money", "1,003,862.14 BRL"),
        (0.0677309, "pct", "6.77%"),
        ("SP", None, "SP"),
        (None, "money", "no value"),
    ],
)
def test_format_value(value, fmt, expected):
    assert format_value(value, fmt) == expected


def test_renders_rows_and_formats():
    text = (
        "{{r1.state}} leads with {{r1.revenue:money}}; {{r2.state}} has {{r2.late_rate:pct}} late."
    )
    assert render(text, RESULT) == "SP leads with 5,202,955.05 BRL; RJ has 13.47% late."


def test_lists_every_problem():
    text = "{{r3.state}} and {{r1.profit}} and {{r1.state:money}} and {{r1.orders:big}}."
    with pytest.raises(TemplateError) as error:
        render(text, RESULT)
    message = str(error.value)
    assert "row 3 does not exist (1 to 2)" in message
    assert "no column profit" in message
    assert "Format money needs a number" in message
    assert "Unknown format big" in message


def test_refuses_numbers_written_out():
    with pytest.raises(TemplateError, match=r"written out.*5\.2 million"):
        render("SP leads with about 5.2 million BRL.", RESULT)


def test_allows_years_ranks_and_numbers_from_the_question():
    question = "Which states have at least 10,000 orders?"
    text = (
        "In 2018, the top 2 states with at least 10,000 orders are {{r1.state}} and {{r2.state}}."
    )
    assert render(text, RESULT, question).endswith("are SP and RJ.")


def test_placeholders_need_a_result():
    with pytest.raises(TemplateError, match="no query result"):
        render("There are {{r1.orders}} orders.", None)


def test_refusal_text_without_numbers_renders_as_is():
    assert render("I can only read data, not delete it.", None) == (
        "I can only read data, not delete it."
    )
