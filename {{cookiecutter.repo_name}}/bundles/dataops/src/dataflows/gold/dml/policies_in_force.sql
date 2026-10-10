SELECT
    product AS product,
    COUNT(*) AS policies_in_force,
    SUM(annual_premium) AS annual_premium
FROM {silver_schema}.dim_policy
WHERE __END_AT IS NULL
  AND status = 'ACTIVE'
GROUP BY product
