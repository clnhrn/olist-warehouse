-- Every seller in silver has exactly one current dim_seller version.
-- Returns a row per seller with none or several.
select
    silver_sellers.seller_id,
    count(dim_seller.seller_key) as current_versions
from {{ ref('silver_sellers') }} as silver_sellers
left join {{ ref('dim_seller') }} as dim_seller
    on
        silver_sellers.seller_id = dim_seller.seller_id
        and dim_seller.is_current
group by silver_sellers.seller_id
having count(dim_seller.seller_key) != 1
