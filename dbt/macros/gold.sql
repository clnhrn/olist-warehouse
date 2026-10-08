{#
    Gold helpers. Gold models declare portable types in their contracts
    (string, int64, bool, date, timestamp, numeric(p, s)), which both DuckDB
    and BigQuery accept, so the same YAML enforces on both adapters.
#}

{#- BigQuery monthly partitioning on a timestamp column. None elsewhere:
    dbt-duckdb reads partition_by as a file layout for external tables. -#}
{% macro monthly_partition(column) -%}
    {%- if target.type == 'bigquery' -%}
        {{ return({'field': column, 'data_type': 'timestamp', 'granularity': 'month'}) }}
    {%- endif -%}
    {{ return(none) }}
{%- endmacro %}


{#- Round to `scale` and cast to the contract's numeric(precision, scale).
    BigQuery allows parameterized types in column definitions but not in
    CAST, so it casts to plain numeric and the contract's DDL applies the
    precision. DuckDB's plain numeric is decimal(18, 3), so it gets the full
    type. -#}
{% macro to_numeric(expression, precision, scale) -%}
    {{ return(adapter.dispatch('to_numeric')(expression, precision, scale)) }}
{%- endmacro %}

{% macro default__to_numeric(expression, precision, scale) -%}
    cast(round({{ expression }}, {{ scale }}) as numeric({{ precision }}, {{ scale }}))
{%- endmacro %}

{% macro bigquery__to_numeric(expression, precision, scale) -%}
    cast(round({{ expression }}, {{ scale }}) as numeric)
{%- endmacro %}


{#- Key of the unknown member every dimension carries. -#}
{% macro unknown_key() -%}
    '-1'
{%- endmacro %}


{#- dim_date key (YYYYMMDD integer) of a date or timestamp, -1 when null. -#}
{% macro date_key(column) -%}
    coalesce(
        cast(
            extract(year from {{ column }}) * 10000
            + extract(month from {{ column }}) * 100
            + extract(day from {{ column }}) as int64
        ),
        -1
    )
{%- endmacro %}


{#- Fractional time between two timestamps, in days or hours, rounded to
    2 decimals. Null when either side is null. -#}
{% macro duration(start_column, end_column, unit='day') -%}
    {%- set seconds = {'day': 86400, 'hour': 3600}[unit] -%}
    {{ to_numeric(dbt.datediff(start_column, end_column, 'second') ~ ' / ' ~ seconds ~ '.0', 10, 2) }}
{%- endmacro %}


{#- Median of a column within a window partition. -#}
{% macro median_over(column, partition_by) -%}
    {{ return(adapter.dispatch('median_over')(column, partition_by)) }}
{%- endmacro %}

{% macro default__median_over(column, partition_by) -%}
    median({{ column }}) over (partition by {{ partition_by }})
{%- endmacro %}

{% macro bigquery__median_over(column, partition_by) -%}
    percentile_cont({{ column }}, 0.5) over (partition by {{ partition_by }})
{%- endmacro %}


{#- ISO 8601 week number (weeks start on Monday). -#}
{% macro iso_week(column) -%}
    {{ return(adapter.dispatch('iso_week')(column)) }}
{%- endmacro %}

{% macro default__iso_week(column) -%}
    extract(week from {{ column }})
{%- endmacro %}

{% macro bigquery__iso_week(column) -%}
    extract(isoweek from {{ column }})
{%- endmacro %}


{#- ISO day of week: 1 = Monday ... 7 = Sunday. -#}
{% macro iso_day_of_week(column) -%}
    {{ return(adapter.dispatch('iso_day_of_week')(column)) }}
{%- endmacro %}

{% macro default__iso_day_of_week(column) -%}
    extract(isodow from {{ column }})
{%- endmacro %}

{#- BigQuery's dayofweek starts at Sunday = 1. -#}
{% macro bigquery__iso_day_of_week(column) -%}
    mod(extract(dayofweek from {{ column }}) + 5, 7) + 1
{%- endmacro %}


{#- English month or day name (format '%B' or '%A'). -#}
{% macro format_date(column, date_format) -%}
    {{ return(adapter.dispatch('format_date')(column, date_format)) }}
{%- endmacro %}

{% macro default__format_date(column, date_format) -%}
    strftime({{ column }}, '{{ date_format }}')
{%- endmacro %}

{% macro bigquery__format_date(column, date_format) -%}
    format_date('{{ date_format }}', {{ column }})
{%- endmacro %}
