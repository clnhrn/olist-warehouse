-- SCD2 shape from the start: one version per seller, valid from the
-- 2016-01-01 baseline (before the first order) and still current. Phase 6
-- swaps the source for a snapshot on a business updated_at; the key recipe
-- (seller_id, valid_from) stays, so baseline keys do not change.
with

versions as (
    select
        seller_id,
        zip_code_prefix,
        city,
        state,
        cast('2016-01-01 00:00:00' as timestamp) as valid_from,
        cast(null as timestamp) as valid_to
    from {{ ref('silver_sellers') }}
)

select
    {{ dbt_utils.generate_surrogate_key(['seller_id', 'valid_from']) }} as seller_key,
    seller_id,
    zip_code_prefix,
    city,
    state,
    {{ dbt_utils.generate_surrogate_key(['zip_code_prefix']) }} as location_key,
    valid_from,
    valid_to,
    valid_to is null as is_current
from versions

union all

select
    {{ unknown_key() }} as seller_key,
    cast(null as string) as seller_id,
    cast(null as string) as zip_code_prefix,
    cast(null as string) as city,
    cast(null as string) as state,
    {{ unknown_key() }} as location_key,
    cast(null as timestamp) as valid_from,
    cast(null as timestamp) as valid_to,
    cast(null as bool) as is_current
