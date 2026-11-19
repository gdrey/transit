import tempfile,unittest
from unittest.mock import patch
import agency_presets,workbench

class PresetTests(unittest.TestCase):
 def test_save_update_remove_and_reject_credentials(self):
  with tempfile.TemporaryDirectory() as d,patch.object(workbench,'ROOT',d):
   original={'name':'Example','gtfs_url':'https://example.org/feed.zip','alert_type':'GTFS Realtime alerts feed','alert_url':'https://example.org/alerts'}
   saved=agency_presets.save(original)
   self.assertEqual(len(agency_presets.list_all()),5)
   saved['gtfs_url']='https://example.org/new.zip';agency_presets.save(saved)
   self.assertEqual(next(x for x in agency_presets.list_all() if x['id']==saved['id'])['gtfs_url'],saved['gtfs_url'])
   for url in ['https://user:password@localhost/feed.zip','https://example.org/feed.zip?api_key=abc']:
    with self.assertRaises(ValueError):agency_presets.save({**original,'name':'Other','gtfs_url':url})
   agency_presets.delete(saved['id']);self.assertEqual(len(agency_presets.list_all()),4)
if __name__=='__main__':unittest.main()
