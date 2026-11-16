import unittest
from app import db, api

class RequestExportTest(unittest.TestCase):
    def test_request_export_stores_a_row(self):
        conn = db.connect()
        export_id = api.request_export(conn, 7)
        row = conn.execute("SELECT user_id FROM exports WHERE id = ?", (export_id,)).fetchone()
        self.assertEqual(row, (7,))

if __name__ == "__main__":
    unittest.main()
