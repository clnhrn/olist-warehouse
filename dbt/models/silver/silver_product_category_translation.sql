{{ config(unique_key='category_name_pt') }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'product_category_translation')) }}
),

typed as (
    select
        trim(product_category_name) as category_name_pt,
        trim(product_category_name_english) as category_name_en,
        _load_date,
        _loaded_at,
        _source_file
    from bronze
),

{{ latest_row_per_key('typed', ['category_name_pt']) }}
