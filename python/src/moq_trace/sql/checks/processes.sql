-- The captured processes, checked before the per-process views read them: one
-- analyzed process selects every event view, so a repeated row would break them.
-- The table declares no keys, so this rejects the duplicates a key would.
SELECT 'processes sharing a process ID' AS defect, count(*) AS count
FROM (SELECT process_id FROM processes GROUP BY ALL HAVING count(*) > 1)
UNION ALL
SELECT 'processes sharing a capture, host, and PID', count(*)
FROM (SELECT capture, hostname, pid FROM processes GROUP BY ALL HAVING count(*) > 1);
