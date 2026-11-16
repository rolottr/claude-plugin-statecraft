from datetime import datetime, timezone
def request_export(conn, user_id):
    cur = conn.execute("INSERT INTO exports (user_id, created_at) VALUES (?, ?)",
                       (user_id, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return cur.lastrowid

def export_status(conn, export_id):
    row = conn.execute("SELECT is_processing, finished_at, failed_at, download_url FROM exports WHERE id = ?",
                       (export_id,)).fetchone()
    is_processing, finished_at, failed_at, url = row
    if failed_at:
        return {"status": "failed"}
    if finished_at:
        return {"status": "done", "url": url}
    if is_processing:
        return {"status": "processing"}
    return {"status": "queued"}
