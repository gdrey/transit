#!/usr/bin/env python3
"""Small local MCP stdio server for GTFS Schedule inspection."""
import json,sys
import workbench,structured_checks

TOOLS=[
 {'name':'feed_summary','description':'Summarize the GTFS Schedule currently loaded in this workspace.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}},
 {'name':'search_routes','description':'Find routes by route ID, short name, or long name.','inputSchema':{'type':'object','properties':{'query':{'type':'string'},'limit':{'type':'integer','minimum':1,'maximum':100}},'additionalProperties':False}},
 {'name':'scheduled_trips','description':'Find scheduled trips for a GTFS route ID and service date. This is not a live-service check.','inputSchema':{'type':'object','properties':{'route_id':{'type':'string'},'date':{'type':'string','description':'Service date YYYY-MM-DD'},'limit':{'type':'integer','minimum':1,'maximum':100}},'required':['route_id','date'],'additionalProperties':False}},
 {'name':'check_skipped_stops','description':'Compare explicit stop-skip scope and local time windows with the loaded schedule. Inconclusive is not proof of reflection.','inputSchema':{'type':'object','properties':{'route_id':{'type':'string'},'direction_id':{'type':'string','enum':['0','1']},'stop_ids':{'type':'array','items':{'type':'string'}},'timezone':{'type':'string'},'windows':{'type':'array','items':{'type':'object','properties':{'start':{'type':'string'},'end':{'type':'string'}},'required':['start','end'],'additionalProperties':False}}},'required':['route_id','direction_id','stop_ids','timezone','windows'],'additionalProperties':False}}
]
def call(name,args):
    if not isinstance(args,dict):raise ValueError('Arguments must be an object.')
    if name=='feed_summary':return workbench.summary()
    if name=='search_routes':return workbench.search_routes(args.get('query',''),args.get('limit',25))
    if name=='scheduled_trips':return workbench.trips_on_date(args['route_id'],args['date'],args.get('limit',25))
    if name=='check_skipped_stops':return structured_checks.check_skips('stops_skipped',args['route_id'],args['direction_id'],args['stop_ids'],args['timezone'],args['windows'])
    raise ValueError('Unknown tool.')
def response(request):
    id=request.get('id');method=request.get('method');params=request.get('params') or {}
    if id is None:return None
    if method=='initialize':
        version=params.get('protocolVersion','2025-03-26')
        if version not in ('2024-11-05','2025-03-26','2025-06-18'):version='2025-03-26'
        result={'protocolVersion':version,'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'transit-gtfs','version':'0.1.0'}}
    elif method=='ping':result={}
    elif method=='tools/list':result={'tools':TOOLS}
    elif method=='tools/call':
        try:result={'content':[{'type':'text','text':json.dumps(call(params.get('name'),params.get('arguments',{})),ensure_ascii=False)}],'isError':False}
        except Exception as e:result={'content':[{'type':'text','text':str(e) or 'Tool failed.'}],'isError':True}
    else:return {'jsonrpc':'2.0','id':id,'error':{'code':-32601,'message':'Method not found'}}
    return {'jsonrpc':'2.0','id':id,'result':result}
def main():
    for line in sys.stdin:
        try:
            request=json.loads(line)
            result=response(request)
            if result is not None:print(json.dumps(result,ensure_ascii=False),flush=True)
        except Exception:print(json.dumps({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid request'}}),flush=True)
if __name__=='__main__':main()
