{{ config(unique_key='product_id') }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'products')) }}
),

-- The source spells "length" as "lenght"; the typo stops here.
typed as (
    select
        trim(product_id) as product_id,
        {{ nullif_blank('product_category_name') }} as category_name_pt,
        cast(product_name_lenght as {{ dbt.type_int() }}) as name_length,
        cast(product_description_lenght as {{ dbt.type_int() }}) as description_length,
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
