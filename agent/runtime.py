"""Wire the agent for a target: settings, schema, warehouse, LLM, and log.

The CLI, the eval runner, and the Chainlit app all build the agent here, so
they run with the same account, limits, and logging.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.config import AgentSettings, Target, agent_settings
from agent.llm import AgentAnswer, AnthropicMessages, Progress, SqlAgent, load_api_key
from agent.query_log import BigQueryQueryLog, LocalQueryLog, QueryLog, log_row, now, record
from agent.schema import load_schema
from agent.warehouse import BigQueryWarehouse, DuckDBWarehouse, gcp_credentials


@dataclass
class Runtime:
    """An agent ready to answer, plus its log.

    Attributes:
        settings: Agent settings.
        agent: The agent.
        log: Where each question is logged.
    """

    settings: AgentSettings
    agent: SqlAgent
    log: QueryLog

    def ask(
        self,
        question: str,
        history: list[tuple[str, str]] | None = None,
        session_id: str | None = None,
        progress: Progress | None = None,
    ) -> AgentAnswer:
        """Answer a question and log it.

        Args:
            question: The user's question.
            history: Earlier (question, explanation) pairs, oldest first.
            session_id: Session id for the log.
            progress: Called with each step as the agent works.

        Returns:
            The agent's answer.
        """
        asked_at = now()
        answer = self.agent.ask(question, history=history, progress=progress)
        record(self.log, log_row(answer, self.settings.target, session_id, asked_at))
        return answer


def build_runtime(target: Target) -> Runtime:
    """Build the agent for a target.

    Args:
        target: Warehouse to query. `prod` works only inside Cloud Run.

    Returns:
        The runtime. BigQuery targets query and log as the agent account.
    """
    settings = agent_settings(target)
    schema = load_schema()
    messages = AnthropicMessages(load_api_key(settings))
    if target is Target.LOCAL:
        agent = SqlAgent(settings, schema, DuckDBWarehouse(settings), messages)
        return Runtime(settings, agent, LocalQueryLog())
    credentials = gcp_credentials(settings)
    warehouse = BigQueryWarehouse(settings, credentials)
    log = BigQueryQueryLog(settings, credentials)
    return Runtime(settings, SqlAgent(settings, schema, warehouse, messages), log)
