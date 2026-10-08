{{ config(unique_key='product_id') }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'products')) }}
),

-- Contract v1 spells "length" as "lenght"; v2 (from batch 2016-02-01) fixed
-- it. Bronze keeps both columns, each batch fills one, and silver merges them.
typed as (
    select
        trim(product_id) as product_id,
        {{ nullif_blank('product_category_name') }} as category_name_pt,
        cast(
            coalesce(product_name_length, product_name_lenght) as {{ dbt.type_int() }}
        ) as name_length,
        cast(
            coalesce(product_description_length, product_description_lenght)
            as {{ dbt.type_int() }}
        ) as description_length,
        cast(product_photos_qty as {{ dbt.type_int() }}) as photos_qty,
        cast(product_weight_g as {{ dbt.type_int() }}) as weight_g,
        cast(product_length_cm as {{ dbt.type_int() }}) as length_cm,
        cast(product_height_cm as {{ dbt.type_int() }}) as height_cm,
        cast(product_width_cm as {{ dbt.type_int() }}) as width_cm,
        _load_date,
        _loaded_at,
        _source_file
    from bronze
),

{{ latest_row_per_key('typed', ['product_id']) }}
