from datetime import datetime, timezone
def request_export(conn, user_id):
    cur = conn.execute("INSERT INTO exports (user_id, created_at) VALUES (?, ?)",
                       (user_id, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return cur.lastrowid
