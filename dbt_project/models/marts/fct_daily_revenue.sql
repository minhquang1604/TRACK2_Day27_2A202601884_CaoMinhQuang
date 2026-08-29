-- Fixed: the customer dimension is expected to hold exactly one active row per
-- customer_id, but that is a business assumption, not something SQL enforces.
-- If the SCD process ever produces two active rows for the same customer_id
-- (e.g. a botched backfill that forgot to close the previous "current" row),
-- a plain LEFT JOIN silently fans out one order into two rows and doubles both
-- completed_order_rows and daily_revenue with no error. `qualify row_number()`
-- keeps exactly one active row per customer_id (most recent valid_from wins)
-- so the join can never fan out, regardless of how many active rows land here.
-- See dbt_project/models/marts/unit_tests.yml ::
-- duplicate_active_customer_row_does_not_inflate_revenue for the test that
-- exposed this failure mode before this fix.

with completed_orders as (
    select *
    from {{ ref('stg_orders') }}
    where status = 'completed'
),
active_customers as (
    select *
    from {{ ref('stg_customers') }}
    where is_active = true
    qualify row_number() over (partition by customer_id order by valid_from desc) = 1
)
select
    o.order_date,
    count(*) as completed_order_rows,
    sum(o.amount_usd) as daily_revenue
from completed_orders o
left join active_customers c
    on o.customer_id = c.customer_id
group by 1
order by 1
