{{ config(unique_key=['review_id', 'order_id']) }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'order_reviews')) }}
),

typed as (
    select
        trim(review_id) as review_id,
        trim(order_id) as order_id,
        cast(review_score as {{ dbt.type_int() }}) as review_score,
        {{ nullif_blank('review_comment_title') }} as review_comment_title,
        {{ nullif_blank('review_comment_message') }} as review_comment_message,
        cast(review_creation_date as timestamp) as review_created_at,
        cast(review_answer_timestamp as timestamp) as review_answered_at,
        _load_date,
        _loaded_at,
        _source_file
    from bronze
),

{{ latest_row_per_key('typed', ['review_id', 'order_id']) }}
