SELECT
    CAST(date_trunc('month', loss_date) AS DATE) AS loss_month,
    cause AS cause,
    COUNT(*) AS claim_count,
    AVG(reserve_amount) AS average_severity
FROM {silver_schema}.fact_claim
GROUP BY 1, cause
