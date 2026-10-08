{{ config(unique_key='seller_id') }}

with

bronze as (
    {{ bronze_since_last_run(source('bronze', 'sellers')) }}
),

typed as (
    {{ typed_sellers('bronze') }}
),

{{ latest_row_per_key('typed', ['seller_id']) }}
