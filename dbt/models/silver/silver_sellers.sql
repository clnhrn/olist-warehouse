{{ config(unique_key='seller_id') }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'sellers')) }}
),

typed as (
    select
        trim(seller_id) as seller_id,
        trim(seller_zip_code_prefix) as zip_code_prefix,
        {{ normalize_text('seller_city') }} as city,
        upper(trim(seller_state)) as state,
        -- From contract v2; null for rows last delivered before it.
        cast(seller_updated_at as timestamp) as updated_at,
        _load_date,
        _loaded_at,
        _source_file
    from bronze
),

{{ latest_row_per_key('typed', ['seller_id']) }}
