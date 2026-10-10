WITH premium AS (
    SELECT CAST(date_trunc('month', period_start) AS DATE) AS month_start, SUM(earned_premium) AS earned_premium
    FROM {silver_schema}.fact_premium
    GROUP BY 1
),
paid AS (
    SELECT CAST(date_trunc('month', paid_at) AS DATE) AS month_start, SUM(amount) AS paid_amount
    FROM {silver_schema}.fact_claim_payment
    GROUP BY 1
),
reserve AS (
    SELECT CAST(date_trunc('month', loss_date) AS DATE) AS month_start, SUM(reserve_amount) AS outstanding_reserve
    FROM {silver_schema}.fact_claim
    GROUP BY 1
)
SELECT
    premium.month_start AS month_start,
    premium.earned_premium AS earned_premium,
    COALESCE(paid.paid_amount, 0) AS paid_amount,
    COALESCE(reserve.outstanding_reserve, 0) AS outstanding_reserve,
    (COALESCE(paid.paid_amount, 0) + COALESCE(reserve.outstanding_reserve, 0)) / NULLIF(premium.earned_premium, 0) AS loss_ratio
FROM premium
LEFT JOIN paid ON paid.month_start = premium.month_start
LEFT JOIN reserve ON reserve.month_start = premium.month_start
