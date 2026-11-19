"""Index the current draft for the schedule explorer, independently of the base feed."""
import copy, hashlib, json, os
import assessment_queue, auto_model, manual_edits, workbench, pipeline_output

def current():
    base,digest,_=manual_edits.source()
    output=pipeline_output.current()
    if output:
        key=output['sha256'];directory=os.path.join(workbench.ROOT,'.data','draft-view');os.makedirs(directory,exist_ok=True)
        db=os.path.join(directory,'pipeline-'+key+'.sqlite')
        if not os.path.exists(db):workbench.import_feed(output['source'],db_path=db)
        return {'available':True,'db_path':db,'key':key,'message':'Pipeline output','service_included':output['service_included'],'manual_included':output['manual_included']}
    service=auto_model._latest_manifest()
    service=service if service and service.get('base_sha256')==digest and auto_model.is_current(service) else None
    items=manual_edits.history(digest)
    for item in items:item['active']=item.get('applied',item.get('active',False))
    active=[x for x in items if x.get('active')]
    if not service and not active: return {'available':False,'message':'No current draft is available.'}
    key=hashlib.sha256(json.dumps({'base':digest,'service':service.get('sha256') if service else None,'manual':[(x['id'],x['edits']) for x in active]},sort_keys=True).encode()).hexdigest()
    directory=os.path.join(workbench.ROOT,'.data','draft-view');os.makedirs(directory,exist_ok=True)
    db=os.path.join(directory,key+'.sqlite');meta=os.path.join(directory,key+'.json')
    if not os.path.exists(db) or not os.path.exists(meta):
        source=service['output'] if service else base
        if active: source=manual_edits.build(source,digest,copy.deepcopy(items))['output']
        workbench.import_feed(source,db_path=db)
        with open(meta,'w',encoding='utf-8') as f:json.dump({'source':source},f)
    kind='Service changes and additional edits' if service and active else ('Service changes' if service else 'Additional edits only')
    return {'available':True,'db_path':db,'key':key,'message':kind,'service_included':bool(service),'manual_included':bool(active)}
