-- One row per zip code prefix from geolocation, customers, and sellers.
-- City and state: the most common spelling in geolocation, falling back to
-- customer and seller addresses for the zips geolocation lacks.
with

addresses as (
    select
        zip_code_prefix,
        city,
        state,
        1 as source_priority
    from {{ ref('silver_geolocation') }}

    union all

    select
        zip_code_prefix,
        city,
        state,
        2 as source_priority
    from {{ ref('silver_customers') }}

    union all

    select
        zip_code_prefix,
        city,
        state,
        2 as source_priority
    from {{ ref('silver_sellers') }}
),

spellings as (
    select
        zip_code_prefix,
        city,
        state,
        min(source_priority) as source_priority,
        count(*) as occurrences
    from addresses
    group by zip_code_prefix, city, state
),

best_spelling as (
    select
        zip_code_prefix,
        city,
        state
    from spellings
    where true
    qualify row_number() over (
        partition by zip_code_prefix
        order by source_priority asc, occurrences desc, city asc, state asc
    ) = 1
),

coordinates as (
    select
        zip_code_prefix,
        {{ median_over('latitude', 'zip_code_prefix') }} as latitude,
        {{ median_over('longitude', 'zip_code_prefix') }} as longitude
    from {{ ref('silver_geolocation') }}
),

zip_coordinates as (
    select distinct
        zip_code_prefix,
        latitude,
        longitude
    from coordinates
)

select
    {{ dbt_utils.generate_surrogate_key(['best_spelling.zip_code_prefix']) }} as location_key,
    best_spelling.zip_code_prefix,
    best_spelling.city,
    best_spelling.state,
    {{ to_numeric('zip_coordinates.latitude', 9, 6) }} as latitude,
    {{ to_numeric('zip_coordinates.longitude', 9, 6) }} as longitude
from best_spelling
left join zip_coordinates
    on best_spelling.zip_code_prefix = zip_coordinates.zip_code_prefix

union all

select
    {{ unknown_key() }} as location_key,
    cast(null as string) as zip_code_prefix,
    cast(null as string) as city,
    cast(null as string) as state,
    {{ to_numeric('null', 9, 6) }} as latitude,
    {{ to_numeric('null', 9, 6) }} as longitude
