import sqlite3
SCHEMA = """
CREATE TABLE exports (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  is_processing INTEGER NOT NULL DEFAULT 0,
  started_at TEXT,
  finished_at TEXT,
  failed_at TEXT,
  error_message TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  download_url TEXT
);
"""
def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn
