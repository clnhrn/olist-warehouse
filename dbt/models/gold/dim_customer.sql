-- One row per person. Silver customer_id is per order; customer_unique_id is
-- the person. Addresses stay on the facts (customer_location_key).
with

orders as (
    select
        silver_customers.customer_unique_id,
        silver_orders.purchased_at
    from {{ ref('silver_orders') }} as silver_orders
    inner join {{ ref('silver_customers') }} as silver_customers
        on silver_orders.customer_id = silver_customers.customer_id
),

people as (
    select
        customer_unique_id,
        cast(min(purchased_at) as date) as first_purchase_date,
        cast(max(purchased_at) as date) as last_purchase_date,
        cast(count(*) as int64) as order_count
    from orders
    group by customer_unique_id
)

select
    {{ dbt_utils.generate_surrogate_key(['customer_unique_id']) }} as customer_key,
    customer_unique_id,
    first_purchase_date,
    last_purchase_date,
    order_count,
    order_count > 1 as is_repeat_customer
from people

union all

select
    {{ unknown_key() }} as customer_key,
    cast(null as string) as customer_unique_id,
    cast(null as date) as first_purchase_date,
    cast(null as date) as last_purchase_date,
    cast(null as int64) as order_count,
    cast(null as bool) as is_repeat_customer
