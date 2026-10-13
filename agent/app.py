"""Streamlit chat UI for the text-to-SQL agent.

Locally:   uv run streamlit run agent/app.py   (choose DuckDB or dev BigQuery)
Cloud Run: the image runs this with OLIST_AGENT_TARGET=prod, as sa-agent-prod.

Each answer shows the explanation (numbers filled in from the result), an
interactive chart when one fits, the result table, and the SQL with its cost.
"""

from __future__ import annotations

import logging
import os
import uuid

import pandas as pd
import streamlit as st

from agent.chart import build_chart, chart_problems
from agent.config import Target, running_on_cloud_run
from agent.llm import AgentAnswer
from agent.runtime import Runtime, build_runtime

logger = logging.getLogger(__name__)

HISTORY_TURNS = 5
EXAMPLES = (
    "What is total revenue, and how many orders is that?",
    "Show monthly revenue in 2017 as a chart.",
    "Which 5 customer states have the highest late delivery rate?",
    "Chart monthly revenue in 2018 for the top 3 customer states, one line per state.",
    "Which product categories have the best average review score?",
)
TARGET_LABELS = {
    Target.LOCAL: "Local DuckDB",
    Target.DEV: "Dev BigQuery",
    Target.PROD: "Prod BigQuery",
}


@st.cache_resource(show_spinner="Starting the agent...")
def get_runtime(target: Target) -> Runtime:
    """Build the agent once per target and share it across sessions.

    Args:
        target: Warehouse to query.

    Returns:
        The runtime.
    """
    return build_runtime(target)


def choose_target() -> Target:
    """Pick the warehouse: fixed on Cloud Run, chosen in the sidebar locally.

    Returns:
        The target.
    """
    if running_on_cloud_run():
        return Target(os.environ.get("OLIST_AGENT_TARGET", Target.PROD))
    options = [Target.LOCAL, Target.DEV]
    return st.sidebar.radio("Warehouse", options, format_func=TARGET_LABELS.get)


def show_answer(answer: AgentAnswer) -> None:
    """Render one answer: explanation, chart, table, and details.

    Args:
        answer: The agent's answer.
    """
    if answer.status == "failed":
        st.error(answer.explanation)
    elif answer.status == "refused":
        st.warning(answer.explanation)
    else:
        st.markdown(answer.explanation.replace("$", r"\$"))

    result = answer.result
    if answer.status == "answered" and result is not None:
        if answer.chart:
            problems = chart_problems(answer.chart, result)
            if problems:
                st.caption(f"No chart: {'; '.join(problems)}.")
            else:
                st.altair_chart(build_chart(answer.chart, result), width="stretch")
        st.dataframe(
            pd.DataFrame(list(result.rows), columns=list(result.columns)),
            hide_index=True,
            width="stretch",
        )

    with st.expander("SQL and details"):
        st.code(answer.sql or "-- no query ran", language="sql")
        cost = f"${answer.cost_usd:.4f}" if answer.cost_usd is not None else "n/a"
        st.caption(
            f"{len(answer.attempts)} query attempt(s) · {answer.bytes_billed:,} bytes billed · "
            f"LLM cost {cost} · {answer.latency_ms / 1000:.1f} s · {answer.model}"
        )
        if answer.explanation_template and answer.explanation_template != answer.explanation:
            st.caption("Explanation as written by the model, before its numbers were filled in:")
            st.code(answer.explanation_template, language=None)
        for number, attempt in enumerate(answer.attempts, start=1):
            if attempt.error:
                st.caption(f"Attempt {number} failed: {attempt.error}")


def main() -> None:
    """Run the app."""
    st.set_page_config(page_title="Olist warehouse agent", page_icon="📦", layout="wide")
    st.title("Ask the Olist warehouse")
    st.caption(
        "Questions become SQL over the gold star schema, run read-only with a byte cap. "
        "Every number in an answer is filled in from the query result."
    )

    target = choose_target()
    st.sidebar.caption(f"Querying: {TARGET_LABELS[target]}")
    state = st.session_state
    state.setdefault("session_id", uuid.uuid4().hex)
    state.setdefault("turns", [])

    st.sidebar.subheader("Try")
    picked = next((q for q in EXAMPLES if st.sidebar.button(q, width="stretch")), None)
    if st.sidebar.button("New conversation", type="secondary"):
        state.turns = []

    for question, answer in state.turns:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            show_answer(answer)

    question = st.chat_input("Ask about orders, revenue, sellers, deliveries, reviews...") or picked
    if not question:
        return
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("Writing and running SQL..."):
        history = [(q, a.explanation) for q, a in state.turns[-HISTORY_TURNS:]]
        answer = get_runtime(target).ask(question, history=history, session_id=state.session_id)
    state.turns.append((question, answer))
    st.rerun()


main()
