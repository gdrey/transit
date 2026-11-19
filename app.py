"""Transit Workbench web app and MCP transport on Vercel."""
import json
import os
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
import subprocess
import uuid

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import Response, FileResponse, JSONResponse, HTMLResponse
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
import psycopg

from backend import temp_sessions as sessions
from backend import hosted_runtime as runtime

ROOT = Path(__file__).resolve().parent
SITE = os.getenv('GTFS_SITE_URL','https://transit-workbench.vercel.app').rstrip('/')
RESOURCE = SITE + '/mcp'
ASSETS = {
    'comparison-mockup.html','diagram.html','agency_presets.js','ai_session.js','manual_edits.js',
    'pipeline_view.js','schedule_compare.js','comparison_marey.js','verification_view.js','workflow_tracker.js',
}
DOWNLOADS = {'pipeline','manual-edits','platform-version'}

class SessionVerifier:
    async def verify_token(self, token):
        def verify():
            try:
                with sessions.connect() as conn:
                    row = sessions.authorize(conn,token)
                if row:
                    return AccessToken(token=token,client_id='transit-session',subject=str(row[0]),
                                       expires_at=int(row[1].timestamp()),resource=RESOURCE,scopes=[])
            except (psycopg.Error, KeyError):
                pass
            return None
        return await anyio.to_thread.run_sync(verify)

mcp = FastMCP('Transit Workbench',stateless_http=True,json_response=True,streamable_http_path='/mcp',
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
              auth=AuthSettings(issuer_url=SITE,resource_server_url=RESOURCE,validate_token_resource=True),
              token_verifier=SessionVerifier())

def engine(token, method, path, body=b'', ai=None, **kwargs):
    with sessions.connect() as conn:
        row = sessions.authorize(conn,token,lock=method=='POST')
        if not row:
            return {'status':401,'type':'application/json'},b'{"error":"Start a new temporary session."}',0
        return runtime.run(conn,row[0],method,path,body,ai,**kwargs)

def tool(method, path, payload=None):
    token = get_access_token()
    if token is None:
        raise ValueError('Connect with a temporary workbench token.')
    try:
        result,body,_ = engine(token.token,method,path,json.dumps(payload).encode() if payload is not None else b'')
        value = json.loads(body)
    except (psycopg.Error,subprocess.TimeoutExpired,RuntimeError):
        raise ValueError('The workbench is busy or unavailable. Retry shortly.') from None
    if result['status'] >= 400:
        raise ValueError(value.get('error','Workbench request failed.'))
    return value

def threaded(function):
    @wraps(function)
    async def invoke(**arguments):
        return await anyio.to_thread.run_sync(lambda:function(**arguments))
    return invoke

@mcp.tool()
@threaded
def list_agencies() -> dict:
    """List source presets. Select an agency before importing its GTFS feed."""
    return tool('GET','/api/agency-presets')

@mcp.tool()
@threaded
def import_agency(agency_id: str) -> dict:
    """Import the selected agency's GTFS Schedule into this temporary session."""
    agencies = tool('GET','/api/agency-presets')['items']
    match = next((item for item in agencies if item['id']==agency_id),None)
    if not match:
        raise ValueError('Unknown agency. Use list_agencies.')
    return tool('POST','/api/import-url',{'url':match['gtfs_url']})

@mcp.tool()
@threaded
def feed_summary() -> dict:
    """Read the currently imported feed's dates, agencies and record counts."""
    return tool('GET','/api/summary')

@mcp.tool()
@threaded
def list_routes(query: str = '') -> list[dict]:
    """List up to 100 routes matching a route ID, short name, or long name."""
    term = query.casefold()
    return [row for row in tool('GET','/api/routes') if any(term in (row.get(k) or '').casefold()
            for k in ('route_id','route_short_name','route_long_name'))][:100]

@mcp.tool()
@threaded
def scheduled_trips(route_id: str, service_date: str, limit: int = 25) -> dict:
    """List scheduled trips for an exact route ID and GTFS service date, not live service."""
    from urllib.parse import urlencode
    return tool('GET','/api/hosted-trips?'+urlencode({'route_id':route_id,'date':service_date,'limit':limit}))

@mcp.tool()
@threaded
def check_skipped_stops(route_id: str, direction_id: str, stop_ids: list[str], timezone: str,
                        windows: list[dict[str,str]]) -> dict:
    """Check resolved skipped stops against the static feed using local ISO start/end windows."""
    return tool('POST','/api/check-structured',{'change_type':'stops_skipped','route_id':route_id,
                'direction_id':direction_id,'stop_ids':stop_ids,'timezone':timezone,'windows':windows})

@mcp.tool()
@threaded
def stage_route_edit(route_id: str, field: str, value: str) -> dict:
    """Stage a route_color (RGB hex) or route_short_name edit. Use an exact GTFS route ID."""
    return tool('POST','/api/hosted-route-edit',{'route_id':route_id,'field':field,'value':value})

@mcp.tool()
@threaded
def review_pipeline() -> dict:
    """Show edits and service changes ready for Run, with blocked items."""
    return tool('GET','/api/pipeline-scope')

@mcp.tool()
@threaded
def run_pipeline() -> dict:
    """Run the selected changes against the original and create a downloadable draft."""
    job = tool('POST','/api/model-job',{})
    return tool('GET','/api/model-job?id='+job['id'])

@mcp.tool()
@threaded
def review_output() -> dict:
    """Verify the exact exported archive and compare it with the original feed."""
    return tool('GET','/api/pipeline-verification')

@mcp.tool()
@threaded
def open_workbench() -> dict:
    """Open the visual workbench to review the same session used by your MCP client."""
    return {'url':SITE,'note':'Open the workbench in the browser where you created this session.'}

mcp_app = mcp.streamable_http_app()

@asynccontextmanager
async def lifespan(_app):
    async with mcp.session_manager.run():
        yield

app = FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)


@app.middleware('http')
async def private_responses(request, call_next):
    origin = request.headers.get('origin')
    if request.url.path.startswith('/mcp') and origin and origin != SITE:
        return JSONResponse({'error':'Origin is not allowed.'},status_code=403)
    result = await call_next(request)
    result.headers['Cache-Control'] = 'private, no-store'
    result.headers['X-Content-Type-Options'] = 'nosniff'
    result.headers['Referrer-Policy'] = 'no-referrer'
    result.headers['X-Frame-Options'] = 'DENY'
    return result

def error(message,status=400):
    return JSONResponse({'error':message},status_code=status)

@app.exception_handler(psycopg.Error)
async def database_error(_request,_error):
    return error('The session database is busy or unavailable. Try again shortly.',503)

@app.exception_handler(ValueError)
async def invalid_request(_request,exc):
    return error(str(exc))

@app.exception_handler(subprocess.TimeoutExpired)
async def work_timeout(_request,_error):
    return error('This operation exceeded the runtime limit. Try a smaller feed or alert batch.',504)

@app.exception_handler(RuntimeError)
async def engine_error(_request,_error):
    return error('The GTFS engine could not complete this operation.',500)

def csrf(request):
    return request.headers.get('origin') == SITE or (
        os.getenv('GTFS_ALLOW_LOCAL_TEST')=='1' and request.headers.get('origin')==str(request.base_url).rstrip('/')
    )

def cookie(request):
    return request.cookies.get(sessions.COOKIE,'')

@app.get('/api/session')
def session_info(request:Request):
    with sessions.connect() as conn:
        row = sessions.authorize(conn,cookie(request))
    return {'expires_at':row[1].isoformat() if row else None}

@app.post('/api/session')
def start_session(request:Request):
    if not csrf(request):
        return error('Origin is not allowed.',403)
    credential = cookie(request)
    with sessions.connect() as conn:
        existing = sessions.authorize(conn,credential)
        if existing:
            return {'expires_at':existing[1].isoformat()}
        credential, expires = sessions.start(conn)
    response = JSONResponse({'expires_at':expires},status_code=201)
    response.set_cookie(sessions.COOKIE,credential,httponly=True,secure=SITE.startswith('https:'),samesite='strict',
                        max_age=sessions.SESSION_HOURS*3600)
    return response

@app.post('/api/session/token')
def session_token(request:Request):
    if not csrf(request):
        return error('Origin is not allowed.',403)
    token = cookie(request)
    with sessions.connect() as conn:
        row = sessions.authorize(conn,token)
    if not row:
        return error('Start a temporary session first.',401)
    return {'url':RESOURCE,'token':token,'expires_at':row[1].isoformat()}

@app.post('/api/session/end')
def end_session(request:Request):
    if not csrf(request):
        return error('Origin is not allowed.',403)
    with sessions.connect() as conn:
        row = sessions.authorize(conn,cookie(request),lock=True)
        if row:
            conn.execute('DELETE FROM transient_gtfs.sessions WHERE id=%s',(row[0],))
    result = Response(status_code=204)
    result.delete_cookie(sessions.COOKIE,httponly=True,secure=SITE.startswith('https:'),samesite='strict')
    return result

@app.get('/api/clean-sessions')
def cleanup(request:Request):
    import secrets
    configured = os.getenv('CRON_SECRET')
    if not configured or not secrets.compare_digest(request.headers.get('authorization',''),'Bearer '+configured):
        return error('Unauthorized',401)
    with sessions.connect() as conn:
        sessions.clean(conn)
    return {'ok':True}

@app.post('/api/upload/{upload}/{part:int}')
async def stage_upload(request:Request,upload:uuid.UUID,part:int):
    if not csrf(request):
        return error('Origin is not allowed.',403)
    if not 0 <= part <= sessions.MAX_BYTES // sessions.PART_BYTES:
        return error('Invalid ZIP part.')
    body = await request.body()
    if not 0 < len(body) <= sessions.PART_BYTES:
        return error('Invalid ZIP part size.')
    def save():
        with sessions.connect() as conn:
            row = sessions.authorize(conn,cookie(request),lock=True)
            if not row:
                return error('Start a temporary session first.',401)
            # Only one upload can be staged per session.
            prefix = f'@{upload.hex}:'
            conn.execute("DELETE FROM transient_gtfs.files WHERE session_id=%s AND name LIKE '@%%' AND name NOT LIKE %s",
                         (row[0],prefix+'%'))
            sessions.put(conn,row[0],f'{prefix}{part:02d}',body)
            sessions.check_size(conn,row[0])
        return Response(status_code=204)
    return await anyio.to_thread.run_sync(save)

@app.post('/api/upload/{upload}/finish')
def finish_upload(request:Request,upload:uuid.UUID,parts:int):
    if not csrf(request):
        return error('Origin is not allowed.',403)
    if not 0 < parts <= (sessions.MAX_BYTES // sessions.PART_BYTES)+1:
        return error('Invalid ZIP part count.')
    with sessions.connect() as conn:
        row = sessions.authorize(conn,cookie(request),lock=True)
        if not row:
            return error('Start a temporary session first.',401)
        prefix = f'@{upload.hex}:'
        entries = conn.execute('SELECT name,size FROM transient_gtfs.files WHERE session_id=%s AND name LIKE %s ORDER BY name',
                               (row[0],prefix+'%')).fetchall()
        if [n for n,_ in entries] != [f'{prefix}{i:02d}' for i in range(parts)] or sum(n for _,n in entries)>sessions.MAX_BYTES:
            return error('The upload is incomplete or too large.')
        with conn.cursor(name='zip_upload') as cur:
            cur.execute('SELECT name,content FROM transient_gtfs.files WHERE session_id=%s AND name LIKE %s ORDER BY name',
                        (row[0],prefix+'%'))
            result,payload,_ = runtime.run(conn,row[0],'POST','/api/import-upload',
                                           ai=request.headers.get('x-gtfs-ai-service'),upload_parts=cur)
        conn.execute('DELETE FROM transient_gtfs.files WHERE session_id=%s AND name LIKE %s',(row[0],prefix+'%'))
        sessions.check_size(conn,row[0])
    return Response(payload,status_code=result['status'],media_type=result['type'])

@app.get('/download/{name}')
def download(request:Request,name:str,part:int=0,expected:str|None=None):
    if name not in DOWNLOADS or not 0<=part<=100:
        return error('Unknown download.',404)
    result,payload,size = engine(cookie(request),'GET','/download/'+name,part=part)
    if result['status']>=400:
        return Response(payload,status_code=result['status'],media_type=result['type'])
    if expected and expected != result['sha256']:
        return error('The draft changed during download. Download it again.',409)
    return Response(payload,media_type='application/zip',
                    headers={'X-GTFS-ZIP-Size':str(size),'X-GTFS-ZIP-SHA256':result['sha256']})

@app.api_route('/api/{path:path}',methods=['GET','POST'])
async def workbench_api(request:Request,path:str):
    if path.startswith('hosted-'):
        return error('Not found.',404)
    if request.method=='POST' and not csrf(request):
        return error('Origin is not allowed.',403)
    raw = await request.body()
    if len(raw)>sessions.PART_BYTES:
        return error('Use the ZIP upload control for large files.',413)
    target = '/api/'+path+('?' + request.url.query if request.url.query else '')
    result,payload,_ = await anyio.to_thread.run_sync(
        lambda:engine(cookie(request),request.method,target,raw,request.headers.get('x-gtfs-ai-service')))
    return Response(payload,status_code=result['status'],media_type=result['type'])

from starlette.routing import Mount, Match

class MCPMount(Mount):
    def matches(self, scope):
        if scope.get('type')=='http' and scope.get('path') in ('/mcp','/.well-known/oauth-protected-resource/mcp'):
            return Match.FULL, {}
        return Match.NONE, {}

app.router.routes.append(MCPMount('/',app=mcp_app))

@app.get('/')
@app.get('/{path}')
def page(path:str='comparison-mockup.html'):
    if path=='session.js':
        return FileResponse(ROOT/'hosted'/'session.js',media_type='application/javascript')
    if path not in ASSETS:
        return error('Not found.',404)
    file = ROOT/'prototype'/path
    if path in {'comparison-mockup.html','diagram.html'}:
        html = file.read_text(encoding='utf-8')
        html = html.replace('<head>','<head><script src="/session.js"></script>',1)
        html = html.replace('GTFS Workbench · Local prototype','GTFS Workbench')
        return HTMLResponse(html)
    return FileResponse(file)

