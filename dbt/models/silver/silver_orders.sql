{{ config(unique_key='order_id') }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'orders')) }}
),

typed as (
    select
        trim(order_id) as order_id,
        trim(customer_id) as customer_id,
        lower(trim(order_status)) as order_status,
        cast(order_purchase_timestamp as timestamp) as purchased_at,
        cast(order_approved_at as timestamp) as approved_at,
        cast(order_delivered_carrier_date as timestamp) as shipped_at,
        cast(order_delivered_customer_date as timestamp) as delivered_at,
        cast(order_estimated_delivery_date as timestamp) as estimated_delivery_at,
        _load_date,
        _loaded_at,
        _source_file
    from bronze
),

{{ latest_row_per_key('typed', ['order_id']) }}
