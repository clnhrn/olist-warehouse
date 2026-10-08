-- Accumulating snapshot: one row per order, including orders with no items,
-- rebuilt from silver so each milestone appears as soon as silver has it.
{{ config(
    partition_by=monthly_partition('purchased_at'),
    cluster_by=['customer_key', 'order_status'],
) }}

with

items as (
    select
        order_id,
        count(*) as item_count,
        count(distinct seller_id) as seller_count,
        sum(price) as items_price_total,
        sum(freight_value) as freight_total
    from {{ ref('silver_order_items') }}
    group by order_id
),

payments as (
    select
        order_id,
        count(*) as payment_count,
        sum(payment_value) as payment_total
    from {{ ref('silver_order_payments') }}
    group by order_id
)

select
    orders.order_id,
    coalesce(dim_customer.customer_key, {{ unknown_key() }}) as customer_key,
    coalesce(dim_location.location_key, {{ unknown_key() }}) as customer_location_key,
    {{ date_key('orders.purchased_at') }} as purchase_date_key,
    {{ date_key('orders.approved_at') }} as approved_date_key,
    {{ date_key('orders.shipped_at') }} as shipped_date_key,
    {{ date_key('orders.delivered_at') }} as delivered_date_key,
    {{ date_key('orders.estimated_delivery_at') }} as estimated_delivery_date_key,
    orders.purchased_at,
    orders.approved_at,
    orders.shipped_at,
    orders.delivered_at,
    orders.estimated_delivery_at,
    orders.order_status,
    cast(coalesce(items.item_count, 0) as int64) as item_count,
    cast(coalesce(items.seller_count, 0) as int64) as seller_count,
    {{ to_numeric('coalesce(items.items_price_total, 0)', 18, 2) }} as items_price_total,
    {{ to_numeric('coalesce(items.freight_total, 0)', 18, 2) }} as freight_total,
    {{ to_numeric(
        'coalesce(items.items_price_total, 0) + coalesce(items.freight_total, 0)', 18, 2
    ) }} as order_total,
    {{ to_numeric('coalesce(payments.payment_total, 0)', 18, 2) }} as payment_total,
    cast(coalesce(payments.payment_count, 0) as int64) as payment_count,
    {{ duration('orders.purchased_at', 'orders.approved_at') }} as days_to_approve,
    {{ duration('orders.purchased_at', 'orders.shipped_at') }} as days_to_ship,
    {{ duration('orders.purchased_at', 'orders.delivered_at') }} as days_to_deliver,
    {{ duration('orders.estimated_delivery_at', 'orders.delivered_at') }} as delivery_delay_days,
    orders.order_status = 'delivered' as is_delivered,
    -- The estimate is a date (midnight), so late means delivered on a later day.
    case
        when orders.delivered_at is not null
            then cast(orders.delivered_at as date) > cast(orders.estimated_delivery_at as date)
    end as is_late,
    orders.order_status = 'canceled' as is_canceled,
    (orders.order_status = 'delivered' and orders.delivered_at is null)
    or (orders.order_status != 'delivered' and orders.delivered_at is not null)
        as has_status_date_mismatch
from {{ ref('silver_orders') }} as orders
left join items
    on orders.order_id = items.order_id
left join payments
    on orders.order_id = payments.order_id
left join {{ ref('silver_customers') }} as silver_customers
    on orders.customer_id = silver_customers.customer_id
left join {{ ref('dim_customer') }} as dim_customer
    on silver_customers.customer_unique_id = dim_customer.customer_unique_id
left join {{ ref('dim_location') }} as dim_location
    on silver_customers.zip_code_prefix = dim_location.zip_code_prefix
