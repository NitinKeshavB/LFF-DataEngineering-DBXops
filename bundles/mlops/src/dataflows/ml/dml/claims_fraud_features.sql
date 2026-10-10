-- Feature table of the claims fraud model: one row per claim, keyed by claim_id (the FeatureLookup key).
--
-- is_fraud_suspected is a SYNTHETIC label for this example, derived below from the features by fixed rules. It is
-- not an investigation outcome. A real project replaces it with labels from the fraud investigation system.
-- Every expression uses only the data itself (no current_date), so the view is deterministic.
WITH payments AS (
    SELECT
        claim_id,
        COUNT(*) AS payment_count,
        SUM(amount) AS total_paid
    FROM {silver_schema}.fact_claim_payment
    GROUP BY claim_id
),
current_policy AS (
    SELECT policy_id, start_date, sum_insured, annual_premium
    FROM {silver_schema}.dim_policy
    WHERE __END_AT IS NULL
),
claim_features AS (
    SELECT
        claim.claim_id AS claim_id,
        claim.policy_id AS policy_id,
        claim.reserve_amount AS claim_reserve,
        DATEDIFF(claim.reported_date, claim.loss_date) AS days_loss_to_report,
        DATEDIFF(claim.loss_date, policy.start_date) AS policy_age_days,
        policy.sum_insured AS sum_insured,
        policy.annual_premium AS annual_premium,
        CAST(claim.reserve_amount / NULLIF(policy.sum_insured, 0) AS DOUBLE) AS reserve_to_sum_insured,
        CAST(
            COUNT(*) OVER (
                PARTITION BY claim.policy_id
                ORDER BY claim.loss_date, claim.claim_id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS INT
        ) AS prior_claim_count,
        CAST(COALESCE(payments.payment_count, 0) AS INT) AS payment_count,
        CAST(COALESCE(payments.total_paid, 0) AS DECIMAL(18, 2)) AS total_paid
    FROM {silver_schema}.fact_claim AS claim
    LEFT JOIN current_policy AS policy ON policy.policy_id = claim.policy_id
    LEFT JOIN payments ON payments.claim_id = claim.claim_id
)
SELECT
    claim_id,
    policy_id,
    claim_reserve,
    days_loss_to_report,
    policy_age_days,
    sum_insured,
    annual_premium,
    reserve_to_sum_insured,
    prior_claim_count,
    payment_count,
    total_paid,
    CAST(
        CASE
            WHEN days_loss_to_report > 30 AND reserve_to_sum_insured > 0.5 THEN 1
            WHEN policy_age_days < 60 AND reserve_to_sum_insured > 0.25 THEN 1
            WHEN prior_claim_count >= 3 THEN 1
            ELSE 0
        END AS INT
    ) AS is_fraud_suspected
FROM claim_features
