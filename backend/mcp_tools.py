"""Model-independent MCP tool definitions over the shared Postgres service.

Transport is responsible for validating OAuth tokens and resolving a workspace.
This module MUST NOT be called with a workspace supplied by a tool argument.
"""
import json,uuid
import cloud_schedule

TOOLS=[
 {'name':'list_feeds','description':'List imported feed versions in your authenticated workspace.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}},
 {'name':'list_routes','description':'List routes for a feed in your authenticated workspace.','inputSchema':{'type':'object','properties':{'feed_id':{'type':'string','format':'uuid'}},'required':['feed_id'],'additionalProperties':False}},
 {'name':'scheduled_trips','description':'List scheduled trip templates for a route and GTFS service date. Does not imply real-time service.','inputSchema':{'type':'object','properties':{'feed_id':{'type':'string','format':'uuid'},'route_id':{'type':'string'},'service_date':{'type':'string','format':'date'},'limit':{'type':'integer','minimum':1,'maximum':250}},'required':['feed_id','route_id','service_date'],'additionalProperties':False}},
 {'name':'trip_stop_times','description':'Show stop times in stop-sequence order for a scheduled trip, including times over 24:00.','inputSchema':{'type':'object','properties':{'feed_id':{'type':'string','format':'uuid'},'trip_id':{'type':'string'}},'required':['feed_id','trip_id'],'additionalProperties':False}},
]

def execute(conn,workspace,name,args):
    """Run only read-only operations scoped to the verified workspace."""
    if not isinstance(args,dict) or any(k in args for k in ('workspace','workspace_id','user_id')):raise ValueError('Invalid arguments')
    if name=='list_feeds' and not args:
        with conn.cursor() as cur:
            cur.execute("SELECT id,source_name,sha256,status,created_at FROM workbench.feed_versions WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 100",(workspace,))
            return {'feeds':[{'id':str(r[0]),'source_name':r[1],'sha256':r[2],'status':r[3],'created_at':r[4].isoformat()} for r in cur.fetchall()]}
    try: feed=uuid.UUID(args.get('feed_id',''))
    except (ValueError,TypeError,AttributeError):raise ValueError('Invalid feed ID')
    if name=='list_routes' and set(args)=={'feed_id'}:
        return {'routes':cloud_schedule.routes(conn,workspace,feed)}
    if name=='scheduled_trips' and {'feed_id','route_id','service_date'}<=set(args) and not set(args)-{'feed_id','route_id','service_date','limit'}:
        if not isinstance(args['route_id'],str) or len(args['route_id'])>512:raise ValueError('Invalid route ID')
        return cloud_schedule.trips_on_date(conn,workspace,feed,args['route_id'],args['service_date'],args.get('limit',250))
    if name=='trip_stop_times' and set(args)=={'feed_id','trip_id'}:
        if not isinstance(args['trip_id'],str) or len(args['trip_id'])>512:raise ValueError('Invalid trip ID')
        return {'stop_times':cloud_schedule.stop_times(conn,workspace,feed,args['trip_id'])}
    raise ValueError('Unknown tool or invalid arguments')

def response(conn,workspace,request):
    """Handle a JSON-RPC request after transport-level authentication."""
    if not isinstance(request,dict) or request.get('jsonrpc')!='2.0':return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request'}}
    if 'id' not in request:return None
    ident=request['id'];method=request.get('method');params=request.get('params') or {}
    if method=='initialize':
        supported={'2025-03-26','2025-06-18','2025-11-25'}
        version=params.get('protocolVersion')
        result={'protocolVersion':version if version in supported else '2025-03-26','capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'transit-workbench','version':'0.2.0'}}
    elif method=='ping':result={}
    elif method=='tools/list':result={'tools':TOOLS}
    elif method=='tools/call':
        try:result={'content':[{'type':'text','text':json.dumps(execute(conn,workspace,params.get('name'),params.get('arguments',{})),ensure_ascii=False)}], 'isError':False}
        except (ValueError,TypeError,OverflowError):result={'content':[{'type':'text','text':'Invalid tool arguments or unavailable feed.'}],'isError':True}
    else:return {'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Method not found'}}
    return {'jsonrpc':'2.0','id':ident,'result':result}
