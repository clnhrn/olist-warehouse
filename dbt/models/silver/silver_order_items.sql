{{ config(unique_key=['order_id', 'order_item_id']) }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'order_items')) }}
),

typed as (
    select
        trim(order_id) as order_id,
        cast(order_item_id as {{ dbt.type_int() }}) as order_item_id,
        trim(product_id) as product_id,
        trim(seller_id) as seller_id,
        cast(shipping_limit_date as timestamp) as shipping_limit_at,
        cast(price as {{ dbt.type_numeric() }}) as price,
        cast(freight_value as {{ dbt.type_numeric() }}) as freight_value,
        _load_date,
        _loaded_at,
        _source_file
    from bronze
),

{{ latest_row_per_key('typed', ['order_id', 'order_item_id']) }}
