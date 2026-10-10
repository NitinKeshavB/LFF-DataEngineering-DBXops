SELECT
    path AS document_path,
    regexp_extract(path, '[^/]+$', 0) AS document_name,
    modificationTime AS modified_at,
    length AS size_bytes,
    concat_ws(
        '\n\n',
        transform(variant_get(doc, '$.document.elements', 'ARRAY<VARIANT>'), e -> variant_get(e, '$.content', 'STRING'))
    ) AS parsed_text,
    CAST(doc:error_status AS STRING) AS parse_error
FROM (
    SELECT path, modificationTime, length, ai_parse_document(content, map('version', '2.0')) AS doc
    FROM STREAM(live.v_policy_pdfs)
)
