{{ config(unique_key=['order_id', 'payment_sequential']) }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'order_payments')) }}
),

typed as (
    select
        trim(order_id) as order_id,
        cast(payment_sequential as {{ dbt.type_int() }}) as payment_sequential,
        lower(trim(payment_type)) as payment_type,
        cast(payment_installments as {{ dbt.type_int() }}) as payment_installments,
        cast(payment_value as {{ dbt.type_numeric() }}) as payment_value,
        _load_date,
        _loaded_at,
        _source_file
    from bronze
),

{{ latest_row_per_key('typed', ['order_id', 'payment_sequential']) }}
