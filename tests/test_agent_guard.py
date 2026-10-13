import pytest

from agent.guard import UnsafeSqlError, guard_sql

KNOWN = frozenset({"fact_orders", "fact_order_items", "dim_date"})


def bq(sql: str, row_limit: int = 1000) -> str:
    return guard_sql(
        sql,
        known_tables=KNOWN,
        dialect="bigquery",
        dataset="dev_gold",
        row_limit=row_limit,
    ).sql


def duck(sql: str, row_limit: int = 1000) -> str:
    return guard_sql(
        sql, known_tables=KNOWN, dialect="duckdb", dataset="dev_gold", row_limit=row_limit
    ).sql


def test_rewrites_gold_to_target_dataset_without_project():
    assert bq("SELECT COUNT(*) FROM gold.fact_orders") == (
        "SELECT COUNT(*) FROM dev_gold.fact_orders LIMIT 1000"
    )


def test_duckdb_rewrite():
    assert duck("SELECT 1 AS x FROM gold.fact_orders") == (
        "SELECT 1 AS x FROM dev_gold.fact_orders LIMIT 1000"
    )


def test_returns_tables_read():
    guarded = guard_sql(
        "SELECT * FROM gold.fact_orders o JOIN gold.dim_date d ON o.purchase_date_key = d.date_key",
        known_tables=KNOWN,
        dialect="duckdb",
        dataset="dev_gold",
        row_limit=10,
    )
    assert guarded.tables == {"fact_orders", "dim_date"}


def test_allows_ctes_and_set_operations():
    sql = duck(
        "WITH monthly AS (SELECT 1 AS m FROM gold.fact_orders) "
        "SELECT m FROM monthly UNION ALL SELECT 2 FROM gold.dim_date"
    )
    assert "dev_gold.fact_orders" in sql
    assert "dev_gold.dim_date" in sql
    assert sql.endswith("LIMIT 1000")


def test_keeps_a_smaller_limit():
    assert duck("SELECT * FROM gold.fact_orders LIMIT 5").endswith("LIMIT 5")


def test_tightens_a_larger_limit():
    assert duck("SELECT * FROM gold.fact_orders LIMIT 5000", row_limit=100).endswith("LIMIT 100")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM gold.fact_orders WHERE TRUE",
        "UPDATE gold.fact_orders SET order_status = 'x' WHERE TRUE",
        "INSERT INTO gold.fact_orders SELECT * FROM gold.fact_orders",
        "MERGE gold.fact_orders t USING gold.fact_orders s ON FALSE "
        "WHEN NOT MATCHED THEN INSERT ROW",
        "CREATE TABLE gold.x AS SELECT * FROM gold.fact_orders",
        "DROP TABLE gold.fact_orders",
        "ALTER TABLE gold.fact_orders ADD COLUMN x INT64",
        "TRUNCATE TABLE gold.fact_orders",
        "CALL proc()",
        "EXPORT DATA OPTIONS(uri='gs://b/*', format='CSV') AS SELECT * FROM gold.fact_orders",
        "GRANT SELECT ON TABLE gold.fact_orders TO 'user:x@y.z'",
    ],
)
def test_refuses_anything_but_select_in_bigquery(sql):
    with pytest.raises(UnsafeSqlError):
        bq(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM gold.fact_orders; DROP TABLE gold.fact_orders",
        "SELECT * INTO copy FROM gold.fact_orders",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('data/bronze/*/*.parquet')",
        "SELECT getenv('ANTHROPIC_API_KEY') FROM gold.fact_orders",
        "PRAGMA database_list",
        "SET enable_external_access = true",
        "ATTACH 'other.duckdb'",
        "COPY (SELECT * FROM gold.fact_orders) TO 'out.csv'",
    ],
)
def test_refuses_writes_files_and_settings_in_duckdb(sql):
    with pytest.raises(UnsafeSqlError):
        duck(sql)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("SELECT * FROM dev_silver.silver_orders", "not allowed"),
        ("SELECT * FROM silver.silver_orders", "not allowed"),
        ("SELECT * FROM other_project.gold.fact_orders", "not allowed"),
        ("SELECT * FROM fact_orders", "not allowed"),
        ("SELECT * FROM gold.fact_secret", "Unknown table"),
        ("SELECT 1", "reads no gold table"),
        ("SELECT * FROM gold.fact_orders WHERE", "does not parse"),
    ],
)
def test_refuses_tables_outside_gold_with_a_reason(sql, reason):
    with pytest.raises(UnsafeSqlError, match=reason):
        duck(sql)


def test_refuses_information_schema():
    with pytest.raises(UnsafeSqlError):
        bq("SELECT * FROM gold.INFORMATION_SCHEMA.TABLES")


def test_cte_named_like_a_table_does_not_hide_a_bare_table():
    # The CTE `x` is fine; the bare `fact_orders` inside it is not.
    with pytest.raises(UnsafeSqlError):
        duck("WITH x AS (SELECT * FROM fact_orders) SELECT * FROM x")
