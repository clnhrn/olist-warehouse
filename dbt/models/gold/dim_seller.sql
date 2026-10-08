-- SCD2: one row per seller version from silver_seller_versions. The key is
-- a hash of (seller_id, valid_from), so baseline keys stay stable when a
-- seller later gets new versions.
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
from {{ ref('silver_seller_versions') }}

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
