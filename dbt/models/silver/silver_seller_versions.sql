-- Every version of every seller, rebuilt from all bronze batches on each run.
-- Bronze is append-only, so the history never depends on when dbt ran: a
-- fresh rebuild gives the same versions as daily incremental runs.
{{ config(materialized='table') }}

with

typed as (
    {{ typed_sellers(source('bronze', 'sellers')) }}
),

-- One row per version. A version is a seller's updated_at; when several
-- batches deliver it (batch 1 and 2 both carry the baseline), the latest
-- batch wins, by _load_date and then _loaded_at.
versions as (
    select * from typed
    where true
    qualify row_number() over (
        partition by seller_id, updated_at
        order by _load_date desc, _loaded_at desc
    ) = 1
)

select
    seller_id,
    zip_code_prefix,
    city,
    state,
    updated_at as valid_from,
    -- Ordered by the business date, not the batch, so a late batch with an
    -- older updated_at still slots in between the right versions.
    lead(updated_at) over (
        partition by seller_id
        order by updated_at
    ) as valid_to,
    _load_date,
    _loaded_at,
    _source_file
from versions
