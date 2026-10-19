CREATE SCHEMA monitoring;
CREATE TABLE monitoring.window (origin_ns BIGINT, start_ns BIGINT, end_ns BIGINT);
CREATE TABLE monitoring.hosts (
    host VARCHAR, role VARCHAR, pid INTEGER,
    offset_ns BIGINT, uncertainty_ns BIGINT, sampled BOOLEAN,
    PRIMARY KEY (host, role)
);
CREATE TABLE monitoring.resources (
    host VARCHAR, role VARCHAR, pid INTEGER, start_ticks UBIGINT,
    monotonic_ns BIGINT, elapsed_ns BIGINT, clock_ticks BIGINT,
    user_ticks UBIGINT, system_ticks UBIGINT, rss_bytes UBIGINT,
    PRIMARY KEY (host, role, elapsed_ns)
);
CREATE TABLE monitoring.threads (
    host VARCHAR, role VARCHAR, pid INTEGER, tid INTEGER, start_ticks UBIGINT, name VARCHAR,
    elapsed_ns BIGINT, clock_ticks BIGINT, user_ticks UBIGINT, system_ticks UBIGINT,
    PRIMARY KEY (host, role, tid, start_ticks, elapsed_ns)
);
CREATE TABLE monitoring.deliveries (
    host VARCHAR, pid INTEGER, connection UBIGINT,
    monotonic_ns BIGINT, elapsed_ns BIGINT, bytes UBIGINT, objects UBIGINT,
    PRIMARY KEY (host, pid, connection, elapsed_ns)
);

CREATE VIEW monitoring.resource_intervals AS
SELECT *, (end_ns - start_ns) / 1e9 AS seconds,
       (user_seconds + system_seconds) / ((end_ns - start_ns) / 1e9) AS cpu_cores
FROM (
    SELECT host, role, pid, elapsed_ns AS end_ns,
           lag(elapsed_ns) OVER samples AS start_ns,
           (user_ticks - lag(user_ticks) OVER samples) / clock_ticks AS user_seconds,
           (system_ticks - lag(system_ticks) OVER samples) / clock_ticks AS system_seconds,
           rss_bytes
    FROM monitoring.resources
    WINDOW samples AS (PARTITION BY host, role, pid, start_ticks ORDER BY elapsed_ns)
) WHERE start_ns IS NOT NULL;

-- A thread's interval needs two samples of the same thread lifetime, so threads
-- that start or exit between samples contribute only their observed intervals.
CREATE VIEW monitoring.thread_intervals AS
SELECT *, (end_ns - start_ns) / 1e9 AS seconds,
       (user_seconds + system_seconds) / ((end_ns - start_ns) / 1e9) AS cpu_cores
FROM (
    SELECT host, role, pid, tid, start_ticks, name, elapsed_ns AS end_ns,
           lag(elapsed_ns) OVER samples AS start_ns,
           (user_ticks - lag(user_ticks) OVER samples) / clock_ticks AS user_seconds,
           (system_ticks - lag(system_ticks) OVER samples) / clock_ticks AS system_seconds
    FROM monitoring.threads
    WINDOW samples AS (PARTITION BY host, role, pid, tid, start_ticks ORDER BY elapsed_ns)
) WHERE start_ns IS NOT NULL;

CREATE VIEW monitoring.delivery_intervals AS
SELECT *, (end_ns - start_ns) / 1e9 AS seconds,
       bytes / ((end_ns - start_ns) / 1e9) AS bytes_per_second,
       objects / ((end_ns - start_ns) / 1e9) AS objects_per_second
FROM (
    SELECT host, pid, connection, elapsed_ns AS end_ns,
           lag(elapsed_ns) OVER samples AS start_ns,
           bytes - lag(bytes) OVER samples AS bytes,
           objects - lag(objects) OVER samples AS objects
    FROM monitoring.deliveries
    WINDOW samples AS (PARTITION BY host, pid, connection ORDER BY elapsed_ns)
) WHERE start_ns IS NOT NULL;

-- Use whole observed intervals, never invent fractional completed objects at
-- window boundaries. Coverage is explicit and may be shorter than the window.
-- A thread's name is its latest within the window; threads may rename themselves.
CREATE VIEW monitoring.thread_summary AS
SELECT host, role, pid, tid, start_ticks, last(name ORDER BY t.end_ns) AS name,
       sum(seconds) AS covered_seconds,
       sum(user_seconds) AS user_cpu_seconds, sum(system_seconds) AS system_cpu_seconds,
       sum(user_seconds + system_seconds) / sum(seconds) AS mean_cpu_cores,
       max(cpu_cores) AS max_cpu_cores
FROM monitoring.thread_intervals AS t, monitoring.window AS w
WHERE t.start_ns >= w.start_ns AND t.end_ns <= w.end_ns GROUP BY host, role, pid, tid, start_ticks;

-- The busiest thread shows a single-thread ceiling that the process total hides.
CREATE VIEW monitoring.resource_summary AS
WITH memory AS (
    SELECT host, role, pid, count(*) AS samples,
           avg(rss_bytes) AS mean_rss_bytes, max(rss_bytes) AS sampled_max_rss_bytes,
           last(rss_bytes ORDER BY elapsed_ns)::BIGINT - first(rss_bytes ORDER BY elapsed_ns)::BIGINT AS rss_growth_bytes
    FROM monitoring.resources, monitoring.window
    WHERE elapsed_ns BETWEEN start_ns AND end_ns GROUP BY host, role, pid
), cpu AS (
    SELECT host, role, pid, sum(seconds) AS covered_seconds,
           sum(user_seconds) AS user_cpu_seconds, sum(system_seconds) AS system_cpu_seconds,
           sum(user_seconds + system_seconds) / sum(seconds) AS mean_cpu_cores,
           max(cpu_cores) AS max_cpu_cores
    FROM monitoring.resource_intervals AS r, monitoring.window AS w
    WHERE r.start_ns >= w.start_ns AND r.end_ns <= w.end_ns GROUP BY host, role, pid
), threads AS (
    SELECT host, role, pid, count(DISTINCT (tid, start_ticks)) AS sampled_threads
    FROM monitoring.threads, monitoring.window
    WHERE elapsed_ns BETWEEN start_ns AND end_ns GROUP BY host, role, pid
), busiest AS (
    SELECT host, role, pid, max(mean_cpu_cores) AS busiest_thread_mean_cpu_cores,
           max(max_cpu_cores) AS busiest_thread_max_cpu_cores
    FROM monitoring.thread_summary GROUP BY host, role, pid
)
SELECT * FROM memory LEFT JOIN cpu USING (host, role, pid)
    LEFT JOIN threads USING (host, role, pid) LEFT JOIN busiest USING (host, role, pid);

CREATE VIEW monitoring.delivery_summary AS
SELECT host, pid, connection, sum(seconds) AS covered_seconds,
       sum(bytes)::UBIGINT AS bytes, sum(objects)::UBIGINT AS objects,
       sum(bytes) / sum(seconds) AS bytes_per_second,
       sum(objects) / sum(seconds) AS objects_per_second
FROM monitoring.delivery_intervals AS d, monitoring.window AS w
WHERE d.start_ns >= w.start_ns AND d.end_ns <= w.end_ns GROUP BY host, pid, connection;
