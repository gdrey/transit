"""Persist the last complete pipeline output and its input/task provenance."""
import hashlib,json,os,tempfile,uuid,datetime
import feed_validation
import manual_edits,workbench

DIR=os.path.join(workbench.DATA_DIR,'pipeline')
def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def create(service_result,items,tasks,progress=None,publish=True,excluded_alerts=None):
    base,base_hash,_=manual_edits.source()
    active=[x for x in items if x.get('applied')]
    path=service_result['output'] if service_result else base
    if active:
        records=[dict(x,active=bool(x.get('applied'))) for x in items]
        path=manual_edits.build(path,base_hash,records)['output']
    output_hash=digest(path)
    if progress:progress('Comparing and checking the original and output ZIPs.')
    verification=feed_validation.compare(base,path,items,service_result)
    if digest(base)!=base_hash or digest(path)!=output_hash:raise ValueError('A feed changed during verification; retry.')
    version=uuid.uuid4().hex;versions=os.path.join(DIR,'versions');os.makedirs(versions,exist_ok=True)
    report=os.path.join(versions,version+'-verification.json')
    with open(report,'w',encoding='utf-8') as f:json.dump(verification,f,ensure_ascii=False)
    result={'version':version,'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':path,'sha256':output_hash,'base_sha256':base_hash,'tasks':tasks,'excluded_alerts':excluded_alerts or [],'service_included':bool(service_result),'manual_included':bool(active),'service':service_result,'verification_path':report,'verification_sha256':digest(report)}
    with open(os.path.join(versions,version+'.json'),'w',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False)
    if publish:commit(result)
    return result

def commit(result):
    os.makedirs(DIR,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='pipeline-',suffix='.json',dir=DIR)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
        os.replace(tmp,os.path.join(DIR,'current.json'))
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def verification(result):
    if not result.get('verification_path') or not result.get('verification_sha256'):raise ValueError('This output predates verification; run the pipeline again.')
    if digest(result['verification_path'])!=result['verification_sha256']:raise ValueError('Verification report hash mismatch.')
    with open(result['verification_path'],encoding='utf-8') as f:data=json.load(f)
    return {'version':result['version'],'original_sha256':result['base_sha256'],'output_sha256':result['sha256'],**data}

def current():
    try:
        with open(os.path.join(DIR,'current.json'),encoding='utf-8') as f:r=json.load(f)
    except FileNotFoundError:return None
    if r['base_sha256']!=manual_edits.source()[1]:return None
    if not os.path.isfile(r['source']) or digest(r['source'])!=r['sha256']:raise ValueError('The pipeline output is missing or failed its integrity check.')
    return r
