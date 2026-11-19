"""Live integration check against the isolated Neon development schema."""
import hashlib
import io
import sys
import uuid
import zipfile
from pathlib import Path
from starlette.testclient import TestClient
from app import app, sessions, SITE

source = Path(sys.argv[1]).read_bytes()
with zipfile.ZipFile(io.BytesIO(source)) as feed:
    original_routes = feed.read('routes.txt')

with TestClient(app,base_url=SITE) as browser:
    origin = {'Origin':SITE}
    def post(path,**kwargs):
        result = browser.post(path,headers=origin,**kwargs)
        assert result.is_success,(path,result.status_code,result.text[:400])
        return result
    post('/api/session')
    try:
        assert browser.get('/').status_code == 200
        assert browser.get('/api/summary').status_code == 400  # nothing preloaded
        token = post('/api/session/token').json()['token']
        def call(method,params=None,number=1,expected=200,authorized=True):
            headers={'Accept':'application/json, text/event-stream'}
            if authorized:
                headers['Authorization']='Bearer '+token
            response = browser.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':number,'method':method,'params':params or {}})
            assert response.status_code==expected,(method,response.status_code,response.text[:400])
            if expected==200:
                payload=response.json()
                assert 'error' not in payload,payload
                assert not payload.get('result',{}).get('isError'),payload
                return payload['result']
        call('tools/list',expected=401,authorized=False)
        call('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
        assert any(t['name']=='run_pipeline' for t in call('tools/list')['tools'])
        print('Session and authenticated MCP handshake passed',flush=True)
        upload=uuid.uuid4()
        chunks=[source[i:i+sessions.PART_BYTES] for i in range(0,len(source),sessions.PART_BYTES)]
        for i,chunk in enumerate(chunks):
            post(f'/api/upload/{upload}/{i}',content=chunk)
        print('Uploaded GTFS ZIP parts',flush=True)
        imported=post(f'/api/upload/{upload}/finish?parts={len(chunks)}').json()
        assert imported['counts']['routes']>0
        assert browser.get('/api/summary').json()['counts']==imported['counts']
        routes=browser.get('/api/routes').json()
        route=routes[0]['route_id']
        call('tools/call',{'name':'stage_route_edit','arguments':{'route_id':route,'field':'route_color','value':'00A999'}})
        job=post('/api/model-job',json={}).json()
        assert browser.get('/api/model-job?id='+job['id']).json()['state']=='completed'
        report=browser.get('/api/pipeline-verification').json()
        assert report.get('output_sha256'),str(report)[:400]
        output = bytearray()
        for i in range(100):
            res=browser.get('/download/pipeline',params={'part':i,'expected':report['output_sha256']})
            assert res.status_code==200,(res.status_code,res.text[:100])
            output.extend(res.content)
            if len(output)==int(res.headers['X-GTFS-ZIP-Size']):
                break
        assert hashlib.sha256(output).hexdigest()==report['output_sha256']
        with zipfile.ZipFile(io.BytesIO(output)) as draft:
            assert b'00A999' in draft.read('routes.txt')
            assert draft.read('routes.txt')!=original_routes
        assert browser.get('/api/draft-view').json()['available']
        isolated = browser.get('/api/summary',headers={'Cookie': 'gtfs_session=' + uuid.uuid4().hex + '.' + 'a'*43})
        assert isolated.status_code==401
        print('Real ZIP import, MCP edit, draft review, download hash and isolation passed',flush=True)
    finally:
        post('/api/session/end')
    call('tools/list',expected=401)
    print('Session ended and MCP access revoked',flush=True)
