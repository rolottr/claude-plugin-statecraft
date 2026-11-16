import unittest
from app import db, api

class RequestExportTest(unittest.TestCase):
    def test_new_export_is_queued(self):
        conn = db.connect()
        export_id = api.request_export(conn, 7)
        self.assertEqual(api.export_status(conn, export_id), {"status": "queued"})

if __name__ == "__main__":
    unittest.main()
