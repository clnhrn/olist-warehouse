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
    key. On incremental runs an incoming row is kept only if silver holds no
    newer row for its key, so the merge leaves newer rows alone. Without
    that, a rerun of an old batch (new _loaded_at, old _load_date) would pass
    the _loaded_at filter and overwrite newer data.
    Only the key and lineage columns of the existing table are read, so a
    model can gain a column (on_schema_change adds it) without a full refresh. -#}
{% macro latest_row_per_key(relation, key_columns) -%}
candidates as (
    select * from {{ relation }}
    where true
    qualify row_number() over (
        partition by {{ key_columns | join(', ') }}
        order by _load_date desc, _loaded_at desc
    ) = 1
)

select candidates.*
from candidates
{%- if is_incremental() %}
left join {{ this }} as existing
    on
    {%- for key in key_columns %}
        {% if not loop.first %}and {% endif %}candidates.{{ key }} = existing.{{ key }}
    {%- endfor %}
where
    existing.{{ key_columns[0] }} is null
    or candidates._load_date > existing._load_date
    or (
        candidates._load_date = existing._load_date
        and candidates._loaded_at > existing._loaded_at
    )
{%- endif %}
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


{#- Sellers typed from bronze, shared by silver_sellers (latest row) and
    silver_seller_versions (every version). Batches under contract v1 have
    no seller_updated_at; they get the 2016-01-01 baseline, before the first
    order, which is also what v2 sends for an unchanged seller. -#}
{% macro typed_sellers(relation) -%}
    select
        trim(seller_id) as seller_id,
        trim(seller_zip_code_prefix) as zip_code_prefix,
        {{ normalize_text('seller_city') }} as city,
        upper(trim(seller_state)) as state,
        coalesce(
            cast(seller_updated_at as timestamp),
            cast('2016-01-01 00:00:00' as timestamp)
        ) as updated_at,
        _load_date,
        _loaded_at,
        _source_file
    from {{ relation }}
{%- endmacro %}
