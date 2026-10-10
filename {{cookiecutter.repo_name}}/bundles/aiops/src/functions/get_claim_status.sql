CREATE OR REPLACE FUNCTION {ai_schema}.get_claim_status(
    claim_id STRING COMMENT 'Claim id, CLM followed by seven digits, for example CLM0000001'
)
RETURNS TABLE (
    claim_id STRING COMMENT 'Claim id',
    status STRING COMMENT 'Claim status: OPEN, SETTLED, CLOSED or DENIED',
    cause STRING COMMENT 'Cause of loss',
    loss_date DATE COMMENT 'Date the loss happened',
    reported_date DATE COMMENT 'Date the claim was reported',
    reserve_amount DECIMAL(18, 2) COMMENT 'Amount currently reserved for the claim'
)
COMMENT 'Returns the status, cause, dates and reserve of one claim from the conformed claims table. Use it for any question about a specific claim id.'
RETURN
    SELECT
        fc.claim_id,
        fc.status,
        fc.cause,
        fc.loss_date,
        fc.reported_date,
        fc.reserve_amount
    FROM {silver_schema}.fact_claim AS fc
    WHERE fc.claim_id = get_claim_status.claim_id
