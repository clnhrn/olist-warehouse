-- Calendar columns (date, year, month, ...) are keywords, kept as names on
-- purpose because they read naturally in queries.
-- noqa: disable=RF04
with

spine as (
    {{ dbt_utils.date_spine(
        datepart='day',
        start_date="cast('2016-01-01' as date)",
        end_date="cast('2021-01-01' as date)",
    ) }}
),

days as (
    select cast(date_day as date) as date_day
    from spine
)

select
    {{ date_key('date_day') }} as date_key,
    date_day as date,
    cast(extract(year from date_day) as int64) as year,
    cast(extract(quarter from date_day) as int64) as quarter,
    cast(extract(month from date_day) as int64) as month,
    {{ format_date('date_day', '%B') }} as month_name,
    {{ format_date('date_day', '%Y-%m') }} as year_month,
    cast({{ iso_week('date_day') }} as int64) as week_of_year,
    cast({{ iso_day_of_week('date_day') }} as int64) as day_of_week,
    {{ format_date('date_day', '%A') }} as day_name,
    {{ iso_day_of_week('date_day') }} in (6, 7) as is_weekend
from days

union all

select
    -1 as date_key,
    cast(null as date) as date,
    cast(null as int64) as year,
    cast(null as int64) as quarter,
    cast(null as int64) as month,
    cast(null as string) as month_name,
    cast(null as string) as year_month,
    cast(null as int64) as week_of_year,
    cast(null as int64) as day_of_week,
    cast(null as string) as day_name,
    cast(null as bool) as is_weekend
