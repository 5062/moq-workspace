-- Every metric sample names a defined metric and carries exactly the lifecycle
-- identity its grain declares, once, and no QUIC span of a copy is negative:
-- data cannot be sent before it is accepted, so a negative one is a provider
-- defect. Phase totals and coverage frames are unique, which no key enforces.
SELECT 'QUIC object metrics are negative' AS defect, count(*) AS count
FROM metrics.samples
WHERE metric IN ('quic_forward_start', 'quic_tail_gap', 'quic_full_span') AND value_ns < 0
UNION ALL
SELECT 'metric samples have invalid identity columns', count(*)
FROM metrics.samples s JOIN metrics.definitions d USING (metric)
WHERE NOT (
  (grain = 'copy' AND rx_trace_id IS NOT NULL AND tx_trace_id IS NOT NULL
    AND packet_trace_id IS NULL AND span_id IS NULL) OR
  (grain = 'packet' AND rx_trace_id IS NULL AND tx_trace_id IS NULL
    AND packet_trace_id IS NOT NULL AND span_id IS NULL) OR
  (grain = 'occurrence' AND rx_trace_id IS NULL AND tx_trace_id IS NULL
    AND packet_trace_id IS NOT NULL AND span_id IS NOT NULL))
UNION ALL
SELECT 'duplicate metric sample identities', count(*)
FROM (
  SELECT metric, rx_trace_id, tx_trace_id, packet_trace_id, span_id
  FROM metrics.samples GROUP BY ALL HAVING count(*) > 1
)
UNION ALL
SELECT 'metric samples of undefined metrics', count(*)
FROM metrics.samples ANTI JOIN metrics.definitions USING (metric)
UNION ALL
SELECT 'duplicate phase totals', count(*)
FROM (SELECT subject, trace_id, phase FROM metrics.phase_totals GROUP BY ALL HAVING count(*) > 1)
UNION ALL
SELECT 'duplicate coverage frames', count(*)
FROM (SELECT object_trace_id, seq FROM model.coverage_frames GROUP BY ALL HAVING count(*) > 1)
UNION ALL
SELECT 'metrics defined twice', count(*)
FROM (SELECT metric FROM metrics.definitions GROUP BY ALL HAVING count(*) > 1)
UNION ALL
SELECT 'metrics summarized twice', count(*)
FROM (SELECT metric FROM metrics.statistics GROUP BY ALL HAVING count(*) > 1)
UNION ALL
SELECT 'metric positions summarized twice', count(*)
FROM (SELECT metric, position FROM metrics.position_statistics GROUP BY ALL HAVING count(*) > 1)
UNION ALL
SELECT 'timeline selections sharing an order', count(*)
FROM (SELECT selection_order FROM metrics.timeline_selections GROUP BY ALL HAVING count(*) > 1)
UNION ALL
SELECT 'objects covered twice', count(*)
FROM (SELECT object_trace_id FROM model.coverage GROUP BY ALL HAVING count(*) > 1)
UNION ALL
SELECT 'coverage packets listed twice for one object', count(*)
FROM (SELECT object_trace_id, packet_trace_id FROM model.coverage_packets GROUP BY ALL HAVING count(*) > 1);
