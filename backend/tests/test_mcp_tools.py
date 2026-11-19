import sys,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mcp_tools

class McpToolsTests(unittest.TestCase):
    def test_read_scopes_to_authenticated_workspace(self):
        feed=str(uuid.uuid4())
        with patch('mcp_tools.cloud_schedule.routes',return_value=[]) as mock:
            result=mcp_tools.execute(None,'verified-workspace','list_routes',{'feed_id':feed})
            self.assertEqual(result,{'routes':[]})
            self.assertEqual(mock.call_args.args[1],'verified-workspace')
    def test_workspace_cannot_be_supplied_by_tool(self):
        with self.assertRaises(ValueError):mcp_tools.execute(None,'verified','list_routes',{'feed_id':str(uuid.uuid4()),'workspace_id':'other'})
    def test_protocol_lists_only_real_implemented_tools(self):
        r=mcp_tools.response(None,'workspace',{'jsonrpc':'2.0','id':1,'method':'tools/list'})
        self.assertEqual([t['name'] for t in r['result']['tools']],['list_feeds','list_routes','scheduled_trips','trip_stop_times'])
    def test_notifications_have_no_response(self):
        self.assertIsNone(mcp_tools.response(None,'workspace',{'jsonrpc':'2.0','method':'notifications/initialized'}))
