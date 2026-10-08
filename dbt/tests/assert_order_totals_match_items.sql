-- fact_orders totals must add up to fact_order_items. Returns a row on mismatch.
with

order_totals as (
    select
        sum(items_price_total) as price,
        sum(freight_total) as freight
    from {{ ref('fact_orders') }}
),

item_totals as (
    select
        sum(price) as price,
        sum(freight_value) as freight
    from {{ ref('fact_order_items') }}
)

select
    order_totals.price as order_price,
    item_totals.price as item_price,
    order_totals.freight as order_freight,
    item_totals.freight as item_freight
from order_totals
cross join item_totals
where
    order_totals.price != item_totals.price
    or order_totals.freight != item_totals.freight
