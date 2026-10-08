{#
    Audit log. Runs as an on-run-end hook, which dbt runs even when the build
    fails, and appends to <ops>.pipeline_runs: one row per model or seed,
    plus one summary row for tests. Same columns as ingestion's rows
    (ingestion/runs.py). The workflow passes its execution id as
    OLIST_RUN_ID, so a run's ingestion and dbt rows share a run id; outside
    the workflow, dbt's invocation_id is the run id.
    Locally, it also copies rows from local ingestion (data/_runs/*.jsonl)
    into DuckDB, once per run id, so both components can be queried together.
#}
{% macro log_pipeline_run(results) %}
    {%- if not execute -%}
        {{ return('') }}
    {%- endif -%}
    {%- set schema = generate_schema_name('ops', none) | trim -%}
    {%- set relation = schema ~ '.pipeline_runs' -%}
    {%- if target.type == 'duckdb' -%}
        {%- do run_query('create schema if not exists ' ~ schema) -%}
    {%- endif -%}
    {%- do run_query(
        'create table if not exists ' ~ relation ~ ' ('
        ~ 'run_id string, component string, layer string, object string, load_date date, '
        ~ 'started_at timestamp, ended_at timestamp, rows_in int64, rows_out int64, '
        ~ 'rows_quarantined int64, status string, message string)'
    ) -%}
    {%- if target.type == 'duckdb' -%}
        {%- do import_local_ingestion_runs(relation) -%}
    {%- endif -%}

    {%- set run_id = env_var('OLIST_RUN_ID', invocation_id) -%}
    {%- set rows = [] -%}
    {%- set tests = {'total': 0} -%}
    {%- for result in results -%}
        {%- set node = result.node -%}
        {%- if node.resource_type in ['model', 'seed', 'snapshot'] -%}
            {%- set rows_out = result.adapter_response.get('rows_affected') -%}
            {%- if rows_out is none or rows_out < 0 -%}
                {%- set rows_out = 'null' -%}
            {%- endif -%}
            {%- do rows.append(
                "(" ~ _audit_text(run_id) ~ ", 'dbt', " ~ _audit_text(node.config.schema or 'silver')
                ~ ", " ~ _audit_text(node.name) ~ ", null, "
                ~ _audit_timing(result, 'started_at') ~ ", " ~ _audit_timing(result, 'completed_at')
                ~ ", null, " ~ rows_out ~ ", null, " ~ _audit_text(result.status)
                ~ ", " ~ _audit_text(result.message) ~ ")"
            ) -%}
        {%- elif node.resource_type in ['test', 'unit_test'] -%}
            {%- do tests.update({
                'total': tests.total + 1,
                result.status: tests.get(result.status, 0) + 1,
            }) -%}
        {%- endif -%}
    {%- endfor -%}
    {%- if tests.total > 0 -%}
        {%- set failed = tests.get('fail', 0) + tests.get('error', 0) -%}
        {%- set summary = 'pass=' ~ tests.get('pass', 0) ~ ' warn=' ~ tests.get('warn', 0)
            ~ ' fail=' ~ tests.get('fail', 0) ~ ' error=' ~ tests.get('error', 0)
            ~ ' skipped=' ~ tests.get('skipped', 0) -%}
        {%- do rows.append(
            "(" ~ _audit_text(run_id) ~ ", 'dbt', 'tests', 'data and unit tests', null, "
            ~ _audit_timestamp(run_started_at) ~ ", " ~ _audit_timestamp(modules.datetime.datetime.utcnow())
            ~ ", " ~ tests.total ~ ", null, null, " ~ _audit_text('fail' if failed else 'pass')
            ~ ", " ~ _audit_text(summary) ~ ")"
        ) -%}
    {%- endif -%}
    {%- if rows -%}
        {%- do run_query('insert into ' ~ relation ~ ' values ' ~ rows | join(', ')) -%}
        {%- do log('Logged ' ~ rows | length ~ ' rows to ' ~ relation ~ ' (run ' ~ run_id ~ ')', info=true) -%}
    {%- endif -%}
    {{ return('') }}
{% endmacro %}


{#- A string literal, or null. One line, no backslashes, at most 1000
    characters, quotes escaped for the adapter. -#}
{% macro _audit_text(value) -%}
    {%- if value is none -%}
        null
    {%- else -%}
        {%- set clean = (value | string | replace('\n', ' ') | replace('\r', ' ') | replace('\\', '/'))[:1000] -%}
        '{{ dbt.escape_single_quotes(clean) }}'
    {%- endif -%}
{%- endmacro %}


{% macro _audit_timestamp(value) -%}
    cast('{{ value.strftime("%Y-%m-%d %H:%M:%S.%f") }}' as timestamp)
{%- endmacro %}


{#- Start or end of the node's execute step; the run start when it never
    executed (skipped, or failed while compiling). -#}
{% macro _audit_timing(result, field) -%}
    {%- set steps = result.timing | selectattr('name', 'equalto', 'execute') | list -%}
    {%- if steps and steps[0][field] -%}
        {{ _audit_timestamp(steps[0][field]) }}
    {%- else -%}
        {{ _audit_timestamp(run_started_at) }}
    {%- endif -%}
{%- endmacro %}


{#- DuckDB only: copy rows that local ingestion wrote to data/_runs/ into
    pipeline_runs, skipping run ids already copied. -#}
{% macro import_local_ingestion_runs(relation) %}
    {%- set runs_glob = env_var('OLIST_LOCAL_RUNS_DIR', '../data/_runs') ~ '/*.jsonl' -%}
    {%- set found = run_query("select count(*) from glob('" ~ runs_glob ~ "')").columns[0].values()[0] -%}
    {%- if found == 0 -%}
        {{ return('') }}
    {%- endif -%}
    {%- do run_query(
        'insert into ' ~ relation ~ ' select * from read_json('
        ~ "'" ~ runs_glob ~ "', format = 'newline_delimited', columns = {"
        ~ "run_id: 'varchar', component: 'varchar', layer: 'varchar', object: 'varchar', "
        ~ "load_date: 'date', started_at: 'timestamp', ended_at: 'timestamp', "
        ~ "rows_in: 'bigint', rows_out: 'bigint', rows_quarantined: 'bigint', "
        ~ "status: 'varchar', message: 'varchar'}) as runs "
        ~ 'where runs.run_id not in (select run_id from ' ~ relation
        ~ " where component = 'ingestion')"
    ) -%}
    {{ return('') }}
{% endmacro %}
