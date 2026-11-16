from datetime import datetime, timezone
from app import storage, mailer

MAX_ATTEMPTS = 3

def now():
    return datetime.now(timezone.utc).isoformat()

def next_export(conn):
    return conn.execute(
        "SELECT id, user_id, attempts FROM exports WHERE is_processing = 0 AND finished_at IS NULL "
        "AND (failed_at IS NULL OR attempts < ?) ORDER BY id LIMIT 1", (MAX_ATTEMPTS,)).fetchone()

def run_once(conn):
    row = next_export(conn)
    if row is None:
        return
    export_id, user_id, attempts = row
    conn.execute("UPDATE exports SET is_processing = 1, started_at = ? WHERE id = ?", (now(), export_id))
    conn.commit()
    try:
        url = storage.build_export(user_id)
        conn.execute("UPDATE exports SET finished_at = ?, download_url = ? WHERE id = ?", (now(), url, export_id))
        conn.commit()
        mailer.send_export_ready(user_id, url)
        conn.execute("UPDATE exports SET is_processing = 0 WHERE id = ?", (export_id,))
        conn.commit()
    except Exception as exc:
        conn.execute("UPDATE exports SET failed_at = ?, error_message = ?, attempts = attempts + 1 WHERE id = ?",
                     (now(), str(exc), export_id))
        conn.commit()
