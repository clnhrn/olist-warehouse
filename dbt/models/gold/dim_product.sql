with

translations as (
    select
        category_name_pt,
        category_name_en
    from {{ ref('silver_product_category_translation') }}

    union all

    -- The 2 categories the source table lacks, unless a later batch adds them.
    select
        seed.category_name_pt,
        seed.category_name_en
    from {{ ref('category_translation_missing') }} as seed
    where seed.category_name_pt not in (
        select source_table.category_name_pt
        from {{ ref('silver_product_category_translation') }} as source_table
    )
)

select
    {{ dbt_utils.generate_surrogate_key(['products.product_id']) }} as product_key,
    products.product_id,
    products.category_name_pt as category_pt,
    case
        when products.category_name_pt is null then 'unknown'
        else translations.category_name_en
    end as category_en,
    cast(products.name_length as int64) as name_length,
    cast(products.description_length as int64) as description_length,
    cast(products.photos_qty as int64) as photos_qty,
    cast(products.weight_g as int64) as weight_g,
    cast(products.length_cm as int64) as length_cm,
    cast(products.height_cm as int64) as height_cm,
    cast(products.width_cm as int64) as width_cm,
    cast(products.length_cm * products.height_cm * products.width_cm as int64) as volume_cm3
from {{ ref('silver_products') }} as products
left join translations
    on products.category_name_pt = translations.category_name_pt

union all

select
    {{ unknown_key() }} as product_key,
    cast(null as string) as product_id,
    cast(null as string) as category_pt,
    'unknown' as category_en,
    cast(null as int64) as name_length,
    cast(null as int64) as description_length,
    cast(null as int64) as photos_qty,
    cast(null as int64) as weight_g,
    cast(null as int64) as length_cm,
    cast(null as int64) as height_cm,
    cast(null as int64) as width_cm,
    cast(null as int64) as volume_cm3
