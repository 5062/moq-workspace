-- A relay can be stopped before a packet or phase records its end. Only
-- unmatched starts after the window closes are accepted.
SELECT 'packet starts without completions inside the analysis window' AS defect, count(*) AS count
FROM quic_packet_start ANTI JOIN quic_packet_end USING (trace_id)
WHERE timestamp_ns <= (SELECT end_ns FROM model.window)
UNION ALL
SELECT 'moq_object_phase contains unmatched phase boundaries inside the analysis window', count(*)
FROM moq_object_phase AS start ANTI JOIN moq_object_phase AS done
  ON done.trace_id = start.trace_id AND done.span_id = start.span_id
 AND done.phase = start.phase AND done.edge = 'done'
WHERE start.edge = 'start' AND start.timestamp_ns <= (SELECT end_ns FROM model.window)
UNION ALL
SELECT 'quic_packet_phase contains unmatched phase boundaries inside the analysis window', count(*)
FROM quic_packet_phase AS start ANTI JOIN quic_packet_phase AS done
  ON done.trace_id = start.trace_id AND done.span_id = start.span_id
 AND done.phase = start.phase AND done.edge = 'done'
WHERE start.edge = 'start' AND start.timestamp_ns <= (SELECT end_ns FROM model.window)
UNION ALL
-- Tables declare no keys, so these reject the duplicates a key would.
SELECT 'selected objects listed twice', count(*)
FROM (SELECT trace_id FROM model.selected_objects GROUP BY ALL HAVING count(*) > 1);
