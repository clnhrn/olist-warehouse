select
    reviews.review_id,
    reviews.order_id,
    coalesce(dim_customer.customer_key, {{ unknown_key() }}) as customer_key,
    {{ date_key('reviews.review_created_at') }} as review_created_date_key,
    {{ date_key('reviews.review_answered_at') }} as review_answered_date_key,
    cast(reviews.review_score as int64) as review_score,
    {{ duration('reviews.review_created_at', 'reviews.review_answered_at', 'hour') }}
        as answer_hours,
    reviews.review_comment_title is not null as has_title,
    reviews.review_comment_message is not null as has_message,
    reviews.review_comment_title,
    reviews.review_comment_message
from {{ ref('silver_order_reviews') }} as reviews
left join {{ ref('silver_orders') }} as orders
    on reviews.order_id = orders.order_id
left join {{ ref('silver_customers') }} as silver_customers
    on orders.customer_id = silver_customers.customer_id
left join {{ ref('dim_customer') }} as dim_customer
    on silver_customers.customer_unique_id = dim_customer.customer_unique_id
