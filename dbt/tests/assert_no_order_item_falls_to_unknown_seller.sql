-- Every order item with a seller finds the seller version valid at purchase
-- time. An item on the unknown seller means a gap in the history (for
-- example, versions lost on a rebuild). Returns one row per such item.
select
    fact.order_id,
    fact.order_item_id,
    items.seller_id,
    fact.purchased_at
from {{ ref('fact_order_items') }} as fact
inner join {{ ref('silver_order_items') }} as items
    on
        fact.order_id = items.order_id
        and fact.order_item_id = cast(items.order_item_id as int64)
where
    items.seller_id is not null
    and fact.seller_key = {{ unknown_key() }}
