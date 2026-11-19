import csv,io,sys,unittest,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cloud_route_edits

class RouteEditsTests(unittest.TestCase):
    def setUp(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            z.writestr('routes.txt','route_id,route_short_name,route_type,route_color\nr1,1,3,FFFFFF\n')
            z.writestr('other.txt',b'unchanged')
        self.base=b.getvalue()
    def test_output_matches_manifest_and_preserves_other_files(self):
        result,manifest=cloud_route_edits.apply(self.base,[{'route_id':'r1','field':'route_color','value':'#00aa88'}])
        self.assertEqual(manifest['changes'][0]['before'],'FFFFFF')
        self.assertEqual(manifest['changes'][0]['after'],'00AA88')
        with zipfile.ZipFile(io.BytesIO(result)) as z:
            self.assertEqual(z.read('other.txt'),b'unchanged')
            self.assertIn(b'00AA88',z.read('routes.txt'))
    def test_conflict_rejected(self):
        op={'route_id':'r1','field':'route_color','value':'000000'}
        with self.assertRaises(ValueError):cloud_route_edits.apply(self.base,[op,op])
