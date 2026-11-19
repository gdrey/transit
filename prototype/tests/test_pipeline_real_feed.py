"""Integration test against an existing feed and service draft; never modifies them.

GTFS_TEST_SERVICE_MANIFEST=/path/to/platform-....json python3 -m unittest discover -s tests
"""
import hashlib, json, os, tempfile, unittest
from unittest.mock import patch
import comparison_data, manual_edits, pipeline_output

class PipelineRealFeedTest(unittest.TestCase):
    def test_combined_output_and_failed_publication(self):
        manifest=os.environ.get('GTFS_TEST_SERVICE_MANIFEST')
        if not manifest:self.skipTest('Set GTFS_TEST_SERVICE_MANIFEST to an existing service draft for the loaded feed.')
        with open(manifest,encoding='utf-8') as f:service=json.load(f)
        base,base_hash,routes=manual_edits.source()
        self.assertEqual(service['base_sha256'],base_hash)
        route=next(r for r in routes if r.get('route_id'))
        item={'id':'verification-test','active':True,'applied':True,'edits':[{'route_id':route['route_id'],'field':'route_color','value':'123456'}]}
        with tempfile.TemporaryDirectory() as d, patch.object(manual_edits,'DIR',os.path.join(d,'edits')), patch.object(pipeline_output,'DIR',os.path.join(d,'pipeline')):
            before=pipeline_output.digest(base)
            with patch('feed_validation.compare',side_effect=ValueError('simulated check failure')):
                with self.assertRaises(ValueError):pipeline_output.create(service,[item],['verification-test'])
            self.assertIsNone(pipeline_output.current());self.assertEqual(before,pipeline_output.digest(base))
            result=pipeline_output.create(service,[item],['verification-test'])
            report=pipeline_output.verification(result)
            self.assertEqual(report['output_sha256'],pipeline_output.digest(result['source']))
            self.assertEqual(report['intent']['route_fields'][0]['output'],'123456')
            self.assertEqual(report['intent']['stop_records_verified'],len(service['records']))
            change=next(c['change_id'] for c in service['changes'] if any(r.get('change_id')==c['change_id'] for r in service['records']))
            diagram=comparison_data.build(change)
            self.assertEqual(diagram['sha256'],result['sha256'])
            self.assertTrue(diagram['pairs'])
            with open(result['source'],'ab') as f:f.write(b'tamper')
            with self.assertRaises(ValueError):pipeline_output.current()

if __name__=='__main__':unittest.main()
