"""SQL checks before anything runs. Defense in depth: IAM is the real barrier.

The query is parsed with sqlglot in the target's dialect, not pattern-matched,
so `SELECT 1; DROP TABLE x` or a write hidden in a CTE is caught. A query
passes only if it is one read-only statement over known gold tables. It is
then rewritten to the target's dataset and given a row limit.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Statement types that write, change schema, or run something other than a
# query. Command covers what sqlglot cannot parse (CALL, EXPORT, GRANT, ...).
_FORBIDDEN_NODES: tuple[type[exp.Expr], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "TruncateTable",
        "Command",
        "Into",
        "Copy",
        "Pragma",
        "Set",
        "Use",
        "Transaction",
        "Commit",
        "Rollback",
    )
    if hasattr(exp, name)
)

# Functions that read files, the environment, or run nested SQL text.
_FORBIDDEN_FUNCTION_PREFIXES = ("read_", "glob", "getenv", "query", "sniff_", "external_query")


class UnsafeSqlError(ValueError):
    """The query failed a guard check. The message tells the LLM how to fix it."""


@dataclass(frozen=True)
class GuardedSql:
    """A query that passed every check, rewritten for the target.

    Attributes:
        sql: SQL to run, with real dataset names and a row limit.
        tables: Gold tables the query reads.
    """

    sql: str
    tables: frozenset[str]


def _parse_one(sql: str, dialect: str) -> exp.Expr:
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as error:
        raise UnsafeSqlError(f"SQL does not parse in {dialect}: {error}") from error
    if len(statements) != 1:
        raise UnsafeSqlError(f"Send exactly one statement, got {len(statements)}.")
    return statements[0]


def _check_read_only(root: exp.Expr) -> None:
    if not isinstance(root, exp.Query):
        raise UnsafeSqlError(f"Only SELECT queries are allowed, got {root.key.upper()}.")
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeSqlError(f"{node.key.upper()} is not allowed; the agent is read-only.")
        if isinstance(node, exp.Func):
            name = (node.sql_name() if not isinstance(node, exp.Anonymous) else node.name).lower()
            if name.startswith(_FORBIDDEN_FUNCTION_PREFIXES):
                raise UnsafeSqlError(f"Function {name} is not allowed.")


def _cte_names(root: exp.Expr) -> set[str]:
    return {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}


def _check_and_rewrite_tables(
    root: exp.Expr, known: frozenset[str], dataset: str
) -> frozenset[str]:
    ctes = _cte_names(root)
    used: set[str] = set()
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise UnsafeSqlError("Table functions are not allowed; read gold tables only.")
        name, db, catalog = table.name.lower(), table.db.lower(), table.catalog
        if not db and not catalog and name in ctes:
            continue
        if "information_schema" in name or db == "information_schema":
            raise UnsafeSqlError("INFORMATION_SCHEMA is not allowed; the schema is in the prompt.")
        if catalog or db != "gold":
            shown = ".".join(p for p in (catalog, table.db, table.name) if p)
            raise UnsafeSqlError(f"Table {shown} is not allowed. Use gold.<table> only.")
        if name not in known:
            raise UnsafeSqlError(f"Unknown table gold.{table.name}. Known: {sorted(known)}.")
        used.add(name)
        # Dataset only, never the project: BigQuery resolves it from the
        # client's project, and the SQL is logged and shown without it.
        table.set("db", exp.to_identifier(dataset))
    if not used:
        raise UnsafeSqlError("The query reads no gold table.")
    return frozenset(used)


def _apply_row_limit(root: exp.Expr, row_limit: int) -> None:
    limit = root.args.get("limit")
    value = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= row_limit:
        return
    root.set("limit", exp.Limit(expression=exp.Literal.number(row_limit)))


def guard_sql(
    sql: str,
    *,
    known_tables: frozenset[str],
    dialect: str,
    dataset: str,
    row_limit: int,
) -> GuardedSql:
    """Check a query and rewrite it for the target warehouse.

    Args:
        sql: SQL written by the LLM, with tables as `gold.<table>`.
        known_tables: Gold table names.
        dialect: sqlglot dialect, `bigquery` or `duckdb`.
        dataset: Real gold dataset or schema, such as `dev_gold`.
        row_limit: Maximum rows the query may return.

    Returns:
        The rewritten SQL and the tables it reads.

    Raises:
        UnsafeSqlError: If the query is not one read-only SELECT over known
            gold tables.
    """
    root = _parse_one(sql, dialect)
    _check_read_only(root)
    tables = _check_and_rewrite_tables(root, known_tables, dataset)
    _apply_row_limit(root, row_limit)
    return GuardedSql(sql=root.sql(dialect=dialect), tables=tables)
