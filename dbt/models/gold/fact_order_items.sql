{{ config(
    partition_by=monthly_partition('purchased_at'),
    cluster_by=['seller_key', 'product_key'],
) }}

with

orders as (
    select
        silver_orders.order_id,
        silver_orders.order_status,
        silver_orders.purchased_at,
        dim_customer.customer_key,
        dim_location.location_key as customer_location_key
    from {{ ref('silver_orders') }} as silver_orders
    left join {{ ref('silver_customers') }} as silver_customers
        on silver_orders.customer_id = silver_customers.customer_id
    left join {{ ref('dim_customer') }} as dim_customer
        on silver_customers.customer_unique_id = dim_customer.customer_unique_id
    left join {{ ref('dim_location') }} as dim_location
        on silver_customers.zip_code_prefix = dim_location.zip_code_prefix
)

select
    items.order_id,
    cast(items.order_item_id as int64) as order_item_id,
    {{ date_key('orders.purchased_at') }} as purchase_date_key,
    {{ date_key('items.shipping_limit_at') }} as shipping_limit_date_key,
    orders.purchased_at,
    coalesce(orders.customer_key, {{ unknown_key() }}) as customer_key,
    coalesce(orders.customer_location_key, {{ unknown_key() }}) as customer_location_key,
    coalesce(dim_product.product_key, {{ unknown_key() }}) as product_key,
    coalesce(dim_seller.seller_key, {{ unknown_key() }}) as seller_key,
    coalesce(dim_seller.location_key, {{ unknown_key() }}) as seller_location_key,
    orders.order_status,
    {{ to_numeric('items.price', 18, 2) }} as price,
    {{ to_numeric('items.freight_value', 18, 2) }} as freight_value,
    {{ to_numeric('items.price + items.freight_value', 18, 2) }} as item_total
from {{ ref('silver_order_items') }} as items
left join orders
    on items.order_id = orders.order_id
left join {{ ref('dim_product') }} as dim_product
    on items.product_id = dim_product.product_id
-- The seller version valid when the order was placed.
left join {{ ref('dim_seller') }} as dim_seller
    on
        items.seller_id = dim_seller.seller_id
        and orders.purchased_at >= dim_seller.valid_from
        and orders.purchased_at < coalesce(dim_seller.valid_to, cast('9999-12-31' as timestamp))
