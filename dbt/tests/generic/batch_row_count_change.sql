{#
    Row count anomaly check on an append-only bronze table: the newest batch
    (_load_date) must be within max_change of the batch before it. A full
    snapshot that suddenly halves (a truncated file) or doubles (a duplicated
    one) warns. Returns the newest batch when it is out of range.
#}
{% test batch_row_count_change(model, max_change=0.5) %}
    {{ config(severity='warn') }}

    with

    batches as (
        select
            _load_date,
            count(*) as row_count
        from {{ model }}
        group by _load_date
    ),

    ranked as (
        select
            _load_date,
            row_count,
            lag(row_count) over (order by _load_date) as previous_count,
            row_number() over (order by _load_date desc) as recency
        from batches
    )

    select
        _load_date,
        row_count,
        previous_count
    from ranked
    where
        recency = 1
        and previous_count is not null
        and abs(row_count - previous_count) > {{ max_change }} * previous_count
{% endtest %}
