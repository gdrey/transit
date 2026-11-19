import os, tempfile, unittest
from unittest.mock import patch
import manual_edits, platform_checks

class FeedEditReflectionTest(unittest.TestCase):
    def test_reflection_is_against_loaded_feed_not_prior_edits(self):
        routes=[{'route_id':'R','route_color':'008080','route_short_name':'R','route_long_name':'','agency_id':'A'}]
        items=[{'id':'one','active':True,'applied':False,'edits':[{'route_id':'R','field':'route_color','value':'000000'}]},
               {'id':'two','active':True,'applied':False,'edits':[{'route_id':'R','field':'route_color','value':'008080'}]},
               {'id':'missing','active':True,'applied':False,'edits':[{'route_id':'X','field':'route_color','value':'000000'}]}]
        with tempfile.TemporaryDirectory() as d,patch.object(manual_edits,'DIR',d),patch.object(manual_edits,'source',return_value=('feed.zip','digest',routes)):
            manual_edits.save(items,'digest',None)
            rows=manual_edits.status()['items']
            self.assertEqual([x['reflection'] for x in rows],['not_reflected','reflected','unknown'])

class PlatformEvidenceTest(unittest.TestCase):
    def test_platform_needs_positive_evidence_in_each_window(self):
        class Cursor:
            def __init__(self,rows):self.rows=rows
            def fetchone(self):return self.rows[0] if self.rows else None
        class DB:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,sql,args):
                if 'select stop_id,stop_name,parent_station' in sql:return Cursor([{'stop_id':args[0],'stop_name':'Station','parent_station':'P'}])
                return Cursor([(1,)])
        base={'contradicting_stop_records':0,'by_stop':{'old':0},'window_counts':[0,0],'sample_records':[],'missing_time_records':0,'frequency_templates_excluded':False}
        target={'missing_time_records':0,'frequency_templates_excluded':False,'served_stops_by_window':[['new'],[]]}
        with patch.object(platform_checks.workbench,'connect',return_value=DB()),patch.object(platform_checks.structured_checks,'check_skips',side_effect=[base,target]):
            result=platform_checks.check('R','0',['old'],'1',['new'],'UTC',[{},{}])
            self.assertEqual(result['status'],'inconclusive')
        target['served_stops_by_window']=[['new'],['new']]
        with patch.object(platform_checks.workbench,'connect',return_value=DB()),patch.object(platform_checks.structured_checks,'check_skips',side_effect=[base,target]):
            result=platform_checks.check('R','0',['old'],'1',['new'],'UTC',[{},{}])
            self.assertEqual(result['status'],'already_modeled')

if __name__=='__main__':unittest.main()
