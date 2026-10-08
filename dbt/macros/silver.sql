{#
    Silver deduplication. Bronze keeps every batch, so silver keeps the latest
    row per key, ordered by _load_date and then _loaded_at.
#}

{#- Bronze rows to process: all of them on a full build, only rows loaded
    since the last run on an incremental one. -#}
{% macro bronze_since_last_run(source_relation) -%}
    select * from {{ source_relation }}
    {%- if is_incremental() %}
    where _loaded_at > (select max(_loaded_at) from {{ this }})
    {%- endif %}
{%- endmacro %}


{#- Ends a model: a `candidates` CTE and the final select, keeping one row per
    key. On incremental runs the current silver rows for the incoming keys
    compete too. Without them, a rerun of an old batch (new _loaded_at, old
    _load_date) would pass the _loaded_at filter and overwrite newer data. -#}
{% macro latest_row_per_key(relation, key_columns) -%}
    {%- set keys = key_columns | join(', ') -%}
candidates as (
    select * from {{ relation }}
    {%- if is_incremental() %}
    union all
    select existing.*
    from {{ this }} as existing
    inner join (select distinct {{ keys }} from {{ relation }}) as incoming
        using ({{ keys }})
    {%- endif %}
)

select * from candidates
where true
qualify row_number() over (
    partition by {{ keys }}
    order by _load_date desc, _loaded_at desc
) = 1
{%- endmacro %}


{#- Lowercase, trimmed, without accents: "São Paulo " -> "sao paulo". -#}
{% macro normalize_text(column) -%}
    {{ return(adapter.dispatch('normalize_text')(column)) }}
{%- endmacro %}

{% macro default__normalize_text(column) -%}
    lower(trim(strip_accents({{ column }})))
{%- endmacro %}

{% macro bigquery__normalize_text(column) -%}
    lower(trim(regexp_replace(normalize({{ column }}, NFD), r'\pM', '')))
{%- endmacro %}


{#- Blank strings become null. -#}
{% macro nullif_blank(column) -%}
    nullif(trim({{ column }}), '')
{%- endmacro %}
