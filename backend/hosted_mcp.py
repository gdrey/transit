"""Hosted read-only MCP; requires a separately configured Neon OAuth server.

Environment: DATABASE_URL, NEON_AUTH_ISSUER, NEON_AUTH_JWKS_URL,
MCP_RESOURCE_URL. Never expose these database credentials to the client.
"""
import asyncio,os
from contextlib import contextmanager
import jwt,psycopg
from jwt import PyJWKClient
from pydantic import AnyHttpUrl
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.middleware.auth_context import get_access_token
import cloud_schedule

ISSUER=os.environ['NEON_AUTH_ISSUER'].rstrip('/')
AUDIENCE=os.environ['MCP_RESOURCE_URL'].rstrip('/')
DATABASE_URL=os.environ['DATABASE_URL']
KEYS=PyJWKClient(os.environ['NEON_AUTH_JWKS_URL'],cache_jwk_set=True,lifespan=300)

class NeonVerifier:
    async def verify_token(self,token):
        def verify():
            try:
                key=KEYS.get_signing_key_from_jwt(token).key
                payload=jwt.decode(token,key,algorithms=['RS256','ES256','EdDSA'],audience=AUDIENCE,issuer=ISSUER,options={'require':['iss','aud','sub','exp']})
                subject=payload['sub']
                if not isinstance(subject,str) or not subject:return None
                scope=payload.get('scope','')
                return AccessToken(token=token,client_id=payload.get('client_id') or payload.get('azp') or 'oauth-client',subject=subject,scopes=scope.split() if isinstance(scope,str) else [],expires_at=payload['exp'],resource=AUDIENCE)
            except (jwt.PyJWTError,ValueError,TypeError,KeyError):return None
        return await asyncio.to_thread(verify)

server=FastMCP('Transit Workbench',stateless_http=True,json_response=True,streamable_http_path='/mcp',auth=AuthSettings(issuer_url=AnyHttpUrl(ISSUER),resource_server_url=AnyHttpUrl(AUDIENCE),validate_token_resource=True),token_verifier=NeonVerifier())

@contextmanager
def session():
    token=get_access_token()
    if not token or not token.subject:raise ValueError('Authentication required')
    with psycopg.connect(DATABASE_URL,connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT workspace_id FROM workbench.workspace_members WHERE subject=%s ORDER BY workspace_id LIMIT 2',(token.subject,))
            rows=cur.fetchall()
        if len(rows)!=1:raise ValueError('A single workspace is required; select a workspace in the workbench.')
        yield conn,rows[0][0]

@server.tool()
def list_feeds()->dict:
    """List the feed versions in your authorized workspace."""
    with session() as (conn,workspace),conn.cursor() as cur:
        cur.execute('SELECT id,source_name,sha256,status,created_at FROM workbench.feed_versions WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 100',(workspace,))
        return {'feeds':[{'id':str(r[0]),'source_name':r[1],'sha256':r[2],'status':r[3],'created_at':r[4].isoformat()} for r in cur.fetchall()]}

@server.tool()
def list_routes(feed_id:str)->dict:
    """List scheduled routes for a feed in your authorized workspace."""
    with session() as (conn,workspace):return {'routes':cloud_schedule.routes(conn,workspace,feed_id)}

@server.tool()
def scheduled_trips(feed_id:str,route_id:str,service_date:str,limit:int=100)->dict:
    """Get scheduled trip templates on a GTFS service date, not live service."""
    with session() as (conn,workspace):return cloud_schedule.trips_on_date(conn,workspace,feed_id,route_id,service_date,limit)

@server.tool()
def trip_stop_times(feed_id:str,trip_id:str)->dict:
    """Get stop times in sequence, preserving GTFS times after midnight."""
    with session() as (conn,workspace):return {'stop_times':cloud_schedule.stop_times(conn,workspace,feed_id,trip_id)}

app=server.streamable_http_app()
