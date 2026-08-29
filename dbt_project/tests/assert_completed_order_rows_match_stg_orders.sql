-- Singular data test (runs against real warehouse data, not fixed fixtures):
-- for each order_date, fct_daily_revenue.completed_order_rows must equal the
-- true count of completed orders in stg_orders for that date, computed
-- independently of the customer dimension join. A non-empty result means the
-- join to active_customers fanned rows out (e.g. more than one active row per
-- customer_id slipped past the `qualify` de-dupe in fct_daily_revenue.sql) and
-- daily_revenue is inflated. Complements the fixed-scenario unit test in
-- models/marts/unit_tests.yml, which proves the same failure mode in isolation
-- but never touches real data.

with true_completed_counts as (
    select
        order_date,
        count(*) as true_completed_order_rows
    from {{ ref('stg_orders') }}
    where status = 'completed'
    group by 1
)

select
    f.order_date,
    f.completed_order_rows,
    t.true_completed_order_rows
from {{ ref('fct_daily_revenue') }} f
join true_completed_counts t
    on f.order_date = t.order_date
where f.completed_order_rows != t.true_completed_order_rows
