{{ config(unique_key='customer_id') }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'customers')) }}
),

typed as (
    select
        trim(customer_id) as customer_id,
        trim(customer_unique_id) as customer_unique_id,
        trim(customer_zip_code_prefix) as zip_code_prefix,
        {{ normalize_text('customer_city') }} as city,
        upper(trim(customer_state)) as state,
        _load_date,
        _loaded_at,
        _source_file
    from bronze
),

{{ latest_row_per_key('typed', ['customer_id']) }}
