import sys,unittest
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cloud_jobs

class JobTests(unittest.TestCase):
    def test_refuses_credential_fields(self):
        with self.assertRaises(ValueError): cloud_jobs.enqueue(MagicMock(),'w','import_feed',{'api_key':'secret'})
    def test_refuses_unknown_jobs(self):
        with self.assertRaises(ValueError): cloud_jobs.enqueue(MagicMock(),'w','unknown',{})
    def test_events_are_scoped(self):
        db=MagicMock();cloud_jobs.event(db,'w','j','Indexed trips')
        cur=db.cursor.return_value.__enter__.return_value
        self.assertEqual(cur.execute.call_args.args[1],('w','j','Indexed trips'))
