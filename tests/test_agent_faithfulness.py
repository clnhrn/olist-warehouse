import pytest

from agent.faithfulness import stated_numbers, unsupported_numbers
from agent.warehouse import QueryResult


def rows(*values):
    return QueryResult(columns=tuple(f"c{i}" for i in range(len(values[0]))), rows=values)


def test_parses_units_and_separators():
    parsed = {n.text: n.value for n in stated_numbers("13.5 million, 1,003,862.14 and 52.1%")}
    assert parsed == {"13.5 million": 13_500_000, "1,003,862.14": 1_003_862.14, "52.1%": 52.1}


def test_skips_dates_and_ids():
    text = "On 2017-11-24, seller 4869f7a5dfa2 sold items."
    assert stated_numbers(text) == []


@pytest.mark.parametrize(
    ("text", "result"),
    [
        ("Revenue was 1,003,862.14 BRL.", rows((1003862.14,))),
        ("Growth was about 52.1%.", rows((0.5205894,))),
        ("Total revenue is 13.5 million BRL.", rows((13494400.74,))),
        ("About 7% arrived late.", rows((0.0677309,))),
        ("There were about 45,000 orders.", rows((45101,))),
        ("It sold 736 items for 138,414.60 BRL.", rows(("seller", 138414.6, 736))),
    ],
)
def test_numbers_backed_by_the_result_pass(text, result):
    assert unsupported_numbers(text, result) == []


def test_flags_numbers_the_model_computed_itself():
    result = rows((11, 7544), (12, 5673))
    assert unsupported_numbers("December fell to 5,673, down 1,871.", result) == ["1,871"]


def test_flags_made_up_numbers():
    assert unsupported_numbers("Revenue was 2,000,000 BRL.", rows((1003862.14,))) == ["2,000,000"]


def test_ignores_years_small_integers_and_numbers_from_the_question():
    question = "Which category with at least 1,000 orders has the most late deliveries?"
    text = "In 2018, among categories with at least 1,000 orders, the top 5 are listed."
    assert unsupported_numbers(text, rows(("x", 0.1)), question) == []


def test_rounding_must_follow_the_written_precision():
    # 6.8% is 6.77% rounded to 1 decimal; 6.9% is not.
    assert unsupported_numbers("6.8% were late.", rows((0.0677309,))) == []
    assert unsupported_numbers("6.9% were late.", rows((0.0677309,))) == ["6.9%"]


def test_numbers_may_come_from_any_query_of_the_turn():
    first, last = rows(("2017-10", 660179.62)), rows((0.5205894,))
    text = "Revenue grew 52.1% from 660,179.62 BRL."
    assert unsupported_numbers(text, last) == ["660,179.62"]
    assert unsupported_numbers(text, [first, last]) == []
