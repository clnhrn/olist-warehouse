-- No business key, so no merge: rebuilt from the latest batch, which is a
-- full snapshot of the file. Exact duplicate rows are removed.
{{ config(materialized='table') }}

with

bronze as (
    select * from {{ source('bronze', 'geolocation') }}
),

latest_batch as (
    select bronze.*
    from bronze
    where bronze._load_date = (select max(all_batches._load_date) from bronze as all_batches)
),

distinct_rows as (
    select distinct
        geolocation_zip_code_prefix,
        geolocation_lat,
        geolocation_lng,
        geolocation_city,
        geolocation_state,
        _load_date
    from latest_batch
)

select
    {{ dbt_utils.generate_surrogate_key([
        'geolocation_zip_code_prefix', 'geolocation_lat', 'geolocation_lng',
        'geolocation_city', 'geolocation_state',
    ]) }} as geolocation_key,
    trim(geolocation_zip_code_prefix) as zip_code_prefix,
    cast(geolocation_lat as {{ dbt.type_float() }}) as latitude,
    cast(geolocation_lng as {{ dbt.type_float() }}) as longitude,
    {{ normalize_text('geolocation_city') }} as city,
    upper(trim(geolocation_state)) as state,
    _load_date
from distinct_rows
