# One-time setup: SFTP connection (Lakeflow mode)

Verified against https://docs.databricks.com/aws/en/ingestion/sftp -- run once, before the first job run
(SQL editor in Catalog Explorer, or any client that can run a CREATE CONNECTION statement):

```sql
CREATE CONNECTION PLACEHOLDER-sftp-connection-name
TYPE sftp
OPTIONS (
  host 'PLACEHOLDER-sftp-host',
  user secret('PLACEHOLDER-scope', 'PLACEHOLDER-username-key'),
  pem_private_key secret('PLACEHOLDER-scope', 'PLACEHOLDER-private-key-key'),
  port '22',
  key_fingerprint 'PLACEHOLDER-key-fingerprint'
)
```

Then put that exact connection name into `src/config/land_partner_feed_sftp.yml`'s `connection` field.

This is the Lakeflow-mode counterpart to `land_broker_extracts` (custom mode, xlsx): both land independently
of the generate_data -> bronze -> silver -> gold chain, side by side, so you can compare the two approaches
for the same kind of job -- see `make new-bundle --help` (or `/new-bundle`) for scaffolding either on a new
bundle.
