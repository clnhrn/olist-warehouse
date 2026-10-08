select
    payments.order_id,
    cast(payments.payment_sequential as int64) as payment_sequential,
    {{ date_key('orders.purchased_at') }} as purchase_date_key,
    coalesce(dim_customer.customer_key, {{ unknown_key() }}) as customer_key,
    payments.payment_type,
    cast(payments.payment_installments as int64) as payment_installments,
    {{ to_numeric('payments.payment_value', 18, 2) }} as payment_value
from {{ ref('silver_order_payments') }} as payments
left join {{ ref('silver_orders') }} as orders
    on payments.order_id = orders.order_id
left join {{ ref('silver_customers') }} as silver_customers
    on orders.customer_id = silver_customers.customer_id
left join {{ ref('dim_customer') }} as dim_customer
    on silver_customers.customer_unique_id = dim_customer.customer_unique_id
