-- Every received datagram none of whose packets reached the trace, named by its
-- peer as `network.datagrams` names it, so it takes that peer's role.
INSERT INTO network.receive_drops BY NAME
WITH dropped AS (
  SELECT connection, datagram, min(timestamp_ns) AS timestamp_ns
  FROM network.wire_packets WHERE direction = 'rx'
  GROUP BY ALL HAVING bool_and(trace_id IS NULL)
), peers AS (
  SELECT DISTINCT peer, role FROM network.datagrams
)
SELECT dropped.timestamp_ns - $origin AS elapsed_ns, endpoint.peer, peers.role
FROM dropped
JOIN network.wire_connections AS joined USING (connection)
CROSS JOIN LATERAL (
  SELECT CASE WHEN contains(joined.peer_address, ':') THEN '[' || joined.peer_address || ']'
              ELSE joined.peer_address END || ':' || joined.peer_port AS peer
) AS endpoint
LEFT JOIN peers USING (peer)
WHERE dropped.timestamp_ns >= $origin;
