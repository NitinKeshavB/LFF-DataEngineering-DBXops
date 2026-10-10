SELECT
    concat(document_name, '#', lpad(CAST(chunk_index AS STRING), 4, '0')) AS chunk_id,
    document_path,
    document_name,
    chunk_index,
    chunk_text,
    modified_at
FROM (
    SELECT
        document_path,
        document_name,
        modified_at,
        posexplode(filter(split(parsed_text, '\n\n'), passage -> length(trim(passage)) > 40)) AS (chunk_index, chunk_text)
    FROM STREAM(live.v_policy_documents)
)
