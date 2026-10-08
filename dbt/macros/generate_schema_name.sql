{#
    Datasets are named by layer: prod builds into silver and gold, every
    other target (dev, local) into dev_silver and dev_gold. dbt's default
    would prefix the target dataset instead (dev_silver -> dev_silver_silver).
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set layer = custom_schema_name or 'silver' -%}
    {%- if target.name == 'prod' -%}
        {{ layer | trim }}
    {%- else -%}
        dev_{{ layer | trim }}
    {%- endif -%}
{%- endmacro %}
