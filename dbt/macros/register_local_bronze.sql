{#
    Local target only: expose the bronze Parquet in data/bronze/ as DuckDB
    views named like the BigQuery tables (dev_bronze.<table>). Sources then
    resolve the same way on both adapters, and unit tests can read their
    columns. Batches under different contract versions have different
    columns, so files are combined by column name. Runs as an on-run-start
    hook; a no-op on BigQuery.
#}
{% macro register_local_bronze() %}
    {%- if target.type != 'duckdb' or not execute -%}
        {{ return('') }}
    {%- endif -%}
    {%- set bronze_dir = env_var('OLIST_LOCAL_BRONZE_DIR', '../data/bronze') -%}
    {%- set sources = graph.sources.values() | selectattr('source_name', 'equalto', 'bronze') | list -%}
    {%- for node in sources -%}
        {%- if loop.first -%}
            {%- do run_query('create schema if not exists ' ~ node.schema) -%}
        {%- endif -%}
        {%- do run_query(
            'create or replace view ' ~ node.schema ~ '.' ~ node.identifier ~ ' as '
            ~ "select * from read_parquet('" ~ bronze_dir ~ '/' ~ node.identifier
            ~ "/load_date=*/data.parquet', hive_partitioning = false, union_by_name = true)"
        ) -%}
    {%- endfor -%}
    {{ return('') }}
{% endmacro %}
