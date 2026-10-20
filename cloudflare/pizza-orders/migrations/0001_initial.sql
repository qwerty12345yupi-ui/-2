CREATE TABLE IF NOT EXISTS order_requests (
  request_id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_requests_created_at
  ON order_requests(created_at);

CREATE TABLE IF NOT EXISTS rate_limits (
  ip_hash TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  request_count INTEGER NOT NULL,
  PRIMARY KEY (ip_hash, window_start)
);

CREATE INDEX IF NOT EXISTS idx_rate_limits_window_start
  ON rate_limits(window_start);
