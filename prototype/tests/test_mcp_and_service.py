import io,json,subprocess,sys,unittest
from unittest.mock import patch
import llm_client

class ServiceTests(unittest.TestCase):
 def test_provider_request_uses_runtime_key_and_structured_output(self):
  captured={}
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):return json.dumps({'choices':[{'message':{'content':'{"ok":true}'}}]}).encode()
  class Opener:
   def open(self,req,timeout):captured['request']=req;return Response()
  with patch.object(llm_client.urllib.request,'build_opener',return_value=Opener()):
   result=llm_client.complete('Interpret test',{'type':'object'},{'url':'http://localhost:1234/v1/chat/completions','model':'test-model','key':'session-key'})
  self.assertEqual(result,{'ok':True})
  self.assertEqual(captured['request'].get_header('Authorization'),'Bearer session-key')
  self.assertEqual(json.loads(captured['request'].data)['response_format']['type'],'json_schema')
 def test_service_rejects_embedded_credentials(self):
  with self.assertRaises(ValueError):llm_client.complete('x',{}, {'url':'https://user:password@localhost/v1/chat/completions','model':'x','key':''})

class MCPTests(unittest.TestCase):
 def test_mcp_initialize_and_tools(self):
  messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26'}},{'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'unknown','arguments':{}}}]
  result=subprocess.run([sys.executable,'mcp_server.py'],input=''.join(json.dumps(x)+'\n' for x in messages),capture_output=True,text=True,check=True)
  rows=[json.loads(x) for x in result.stdout.splitlines()]
  self.assertEqual(rows[0]['result']['serverInfo']['name'],'transit-gtfs')
  self.assertIn('scheduled_trips',[x['name'] for x in rows[1]['result']['tools']])
  self.assertTrue(rows[2]['result']['isError'])
if __name__=='__main__':unittest.main()
