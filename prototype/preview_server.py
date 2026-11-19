#!/usr/bin/env python3
"""Local-only GTFS preview and URL importer; not a public web service."""
import base64,copy,http.server,ipaddress,json,os,socket,tempfile,threading,urllib.parse,urllib.request,zipfile,uuid
from html.parser import HTMLParser
import diagram_data,workbench,alert_analysis,assistant_bridge
import structured_checks
import realtime_alerts
import assessment_queue
import review_changes
import platform_edit
import comparison_data
import auto_model
import operation_lock
import manual_edits
import draft_view
import pipeline_output
import pipeline_scope
import agency_presets

LOCK=operation_lock.LOCK
ASSISTANT_LOCK=assessment_queue.ASSISTANT_LOCK
MAX_BYTES=100*1024*1024
MODEL_JOBS={}
MODEL_JOBS_LOCK=threading.Lock()

def check_url(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):
        raise ValueError('Enter a public HTTPS URL without credentials or a custom port.')
    for item in socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(item[4][0]).is_global:
            raise ValueError('The URL must resolve to a public address.')
    return url

def previous_feed_context():
    try:
        _,digest,_=manual_edits.source()
        with workbench.connect() as db:
            agencies=sorted((r['agency_id'] or '',r['agency_name'] or '',r['agency_timezone'] or '') for r in db.execute('select * from agency'))
        return digest,agencies
    except (ValueError,OSError):return None,[]

def after_feed_import(context,service=None):
    digest,agencies=context
    with workbench.connect() as db:
        current=sorted((r['agency_id'] or '',r['agency_name'] or '',r['agency_timezone'] or '') for r in db.execute('select * from agency'))
    if not agencies or current!=agencies:return {'edits_carried':False,'alerts_queued':0}
    carried=manual_edits.carry_forward(digest,agencies)
    return {'edits_carried':carried,'alerts_queued':assessment_queue.recheck_previous_feed(service)}

class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        check_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

class PageText(HTMLParser):
    def __init__(self):
        super().__init__(); self.hidden=0; self.current=[]; self.items=[]; self.in_text=0; self.dynamic=False
    def handle_starttag(self,tag,attrs):
        if tag=='rider-alerts': self.dynamic=True
        if tag in ('script','style','nav','header','footer'): self.hidden+=1
        if not self.hidden and tag in ('p','li','h1','h2','h3','h4','article'):
            if not self.in_text: self.current=[]
            self.in_text+=1
    def handle_endtag(self,tag):
        if tag in ('script','style','nav','header','footer') and self.hidden: self.hidden-=1
        if not self.hidden and tag in ('p','li','h1','h2','h3','h4','article') and self.in_text:
            self.in_text-=1
            if not self.in_text:
                value=' '.join(' '.join(self.current).split())
                if 20<=len(value)<=3000: self.items.append(value)
    def handle_data(self,data):
        if not self.hidden and self.in_text: self.current.append(data)

class Handler(http.server.BaseHTTPRequestHandler):
    def ai_service(self):
        value=self.headers.get('X-GTFS-AI-Service','')
        if not value:return None
        if len(value)>8192:raise ValueError('AI service details are too long.')
        try:return json.loads(base64.b64decode(value,validate=True).decode('utf-8'))
        except Exception:raise ValueError('Invalid AI service settings.')

    def do_POST(self):
        if self.path in ('/api/agency-presets','/api/agency-presets/delete'): return self.agency_presets()
        if self.path=='/api/import-upload': return self.import_upload()
        if self.path in ('/api/manual-edits','/api/manual-edits/toggle'): return self.manual_edits()
        if self.path=='/api/clarify-alert': return self.clarify_alert()
        if self.path=='/api/review-decision': return self.review_decision()
        if self.path=='/api/model-selected': return self.model_selected()
        if self.path=='/api/model-job': return self.start_model_job()
        if self.path=='/api/import-alerts': return self.import_alerts()
        if self.path=='/api/reassess-alerts': return self.reassess_alerts()
        if self.path=='/api/assess-next': return self.assess_next()
        if self.path=='/api/analyze-alert': return self.analyze_alert()
        if self.path=='/api/check-structured': return self.check_structured()
        if self.path!='/api/import-url': return self.send(404,b'Not found','text/plain')
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>4096: raise ValueError('Invalid request size.')
            url=json.loads(self.rfile.read(length)).get('url','')
            check_url(url)
            if not LOCK.acquire(blocking=False): raise ValueError('Another import is in progress.')
            try:
                previous=previous_feed_context()
                directory=workbench.DATA_DIR;os.makedirs(directory,exist_ok=True)
                fd,temp=tempfile.mkstemp(prefix='download-',suffix='.zip',dir=directory); os.close(fd)
                try:
                    opener=urllib.request.build_opener(Redirects())
                    with opener.open(urllib.request.Request(url,headers={'User-Agent':'GTFS-Workbench-Local/0.1'}),timeout=45) as response,open(temp,'wb') as output:
                        total=0
                        while True:
                            chunk=response.read(1024*1024)
                            if not chunk: break
                            total+=len(chunk)
                            if total>MAX_BYTES: raise ValueError('ZIP exceeds the 100 MB download limit.')
                            output.write(chunk)
                    with zipfile.ZipFile(temp) as archive:
                        files=archive.infolist()
                        if len(files)>2000 or sum(x.file_size for x in files)>1024*1024*1024: raise ValueError('Archive exceeds local processing limits.')
                    import hashlib
                    h=hashlib.sha256()
                    with open(temp,'rb') as f:
                        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
                    final=os.path.join(directory,'feed-'+h.hexdigest()+'.zip')
                    os.replace(temp,final)
                    result=workbench.import_feed(final,source_url=url)
                    result['recheck']=after_feed_import(previous,self.ai_service())
                    result['source_url']=url
                    self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
                finally:
                    if os.path.exists(temp): os.unlink(temp)
            finally: LOCK.release()
        except (ValueError,KeyError,OSError,zipfile.BadZipFile,json.JSONDecodeError) as e:
            self.send(400,json.dumps({'error':str(e)}).encode(),'application/json; charset=utf-8')
    def agency_presets(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>10000:raise ValueError('Invalid request size.')
            request=json.loads(self.rfile.read(length))
            if self.path.endswith('/delete'):agency_presets.delete(request.get('id'))
            else:agency_presets.save(request)
            self.send(200,json.dumps({'items':agency_presets.list_all()},ensure_ascii=False).encode(),'application/json; charset=utf-8')
        except (ValueError,OSError,json.JSONDecodeError,TypeError) as e:self.send(400,json.dumps({'error':str(e)}).encode(),'application/json; charset=utf-8')
    def import_upload(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>MAX_BYTES: raise ValueError('Choose a GTFS ZIP no larger than 100 MB.')
            if not LOCK.acquire(blocking=False): raise ValueError('Another feed operation is in progress.')
            try:
                import hashlib
                previous=previous_feed_context()
                directory=workbench.DATA_DIR;os.makedirs(directory,exist_ok=True)
                fd,temp=tempfile.mkstemp(prefix='upload-',suffix='.zip',dir=directory)
                try:
                    digest=hashlib.sha256();remaining=length
                    with os.fdopen(fd,'wb') as output:
                        while remaining:
                            chunk=self.rfile.read(min(1024*1024,remaining))
                            if not chunk: raise ValueError('The upload was incomplete. Please retry.')
                            output.write(chunk);digest.update(chunk);remaining-=len(chunk)
                    with zipfile.ZipFile(temp) as archive:
                        files=archive.infolist()
                        if len(files)>2000 or sum(x.file_size for x in files)>1024*1024*1024: raise ValueError('Archive exceeds local processing limits.')
                    final=os.path.join(directory,'feed-'+digest.hexdigest()+'.zip')
                    os.replace(temp,final)
                    result=workbench.import_feed(final)
                    result['recheck']=after_feed_import(previous,self.ai_service())
                    self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
                finally:
                    if os.path.exists(temp): os.unlink(temp)
            finally: LOCK.release()
        except (ValueError,OSError,zipfile.BadZipFile) as e:
            self.send(400,json.dumps({'error':str(e)}).encode(),'application/json; charset=utf-8')
    def import_alerts(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>200000: raise ValueError('Alert input is too large.')
            request=json.loads(self.rfile.read(length)); mode=request.get('mode'); value=request.get('value','').strip()
            if not value: raise ValueError('Enter a source URL or paste alert text.')
            if mode=='Paste alert text':
                items=[{'text':value,'source':'pasted text','status':'not checked'}]; note='Imported one pasted item for review.'
            elif mode in ('MTA New York Subway', 'GTFS Realtime alerts feed'):
                check_url(value); opener=urllib.request.build_opener(Redirects())
                with opener.open(urllib.request.Request(value,headers={'User-Agent':'GTFS-Workbench-Local/0.1'}),timeout=30) as response:
                    raw=response.read(10*1024*1024+1)
                if len(raw)>10*1024*1024: raise ValueError('Feed exceeds the 10 MB intake limit.')
                items,timestamp=realtime_alerts.decode(raw,value)
                snapshot=realtime_alerts.store(raw,value,items,timestamp)
                note='Imported '+str(len(items))+' alerts into local snapshot '+str(snapshot)+'. Select a batch size to start assessment.'
            elif mode=='Alerts webpage':
                check_url(value); opener=urllib.request.build_opener(Redirects())
                with opener.open(urllib.request.Request(value,headers={'User-Agent':'GTFS-Workbench-Local/0.1'}),timeout=30) as response:
                    raw=response.read(2*1024*1024+1)
                    if len(raw)>2*1024*1024: raise ValueError('Page exceeds the 2 MB intake limit.')
                    html=raw.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
                parser=PageText();parser.feed(html)
                if parser.dynamic: raise ValueError('This page loads alerts dynamically. Its alert endpoint returned HTTP 403 in our test, so a connector is needed. Paste individual alert text for now.')
                items=[{'text':x,'source':value,'status':'not checked'} for x in list(dict.fromkeys(parser.items))[:100]]
                if not items: raise ValueError('No readable page text found; this page may require a source-specific connector.')
                note='Extracted page text for review. It may include non-alert content; select only relevant items.'
            else: raise ValueError('This source type is not supported yet. Use pasted text or a readable webpage.')
            if mode not in ('MTA New York Subway', 'GTFS Realtime alerts feed'):
                import hashlib
                for i,item in enumerate(items):
                    item['id']='alert-'+hashlib.sha256((item['source']+'\0'+item['text']+'\0'+str(i)).encode()).hexdigest()[:16]
                snapshot=realtime_alerts.store(value.encode('utf-8'),value if mode=='Alerts webpage' else 'pasted text',items,None)
            batch=assessment_queue.create(snapshot)
            self.send(200,json.dumps({'items':items,'note':note,'batch_id':batch},ensure_ascii=False).encode(),'application/json; charset=utf-8')
        except (ValueError,OSError,json.JSONDecodeError) as e:
            self.send(400,json.dumps({'error':str(e)}).encode(),'application/json; charset=utf-8')
    def assess_next(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>4096: raise ValueError('Invalid request size.')
            request=json.loads(self.rfile.read(length))
            result=assessment_queue.trigger(request.get('limit',10),self.ai_service())
            self.send(200,json.dumps(result).encode(),'application/json')
        except (ValueError,OSError,json.JSONDecodeError) as e:
            self.send(400,json.dumps({'error':str(e)}).encode(),'application/json')
    def clarify_alert(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>10000:raise ValueError('Invalid request size.')
            request=json.loads(self.rfile.read(length))
            result=assessment_queue.clarify(request.get('batch_id'),request.get('entity_id'),request.get('text'),self.ai_service())
            self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
        except (ValueError,TypeError,OSError,json.JSONDecodeError) as e:self.send(400,json.dumps({'error':str(e)}).encode(),'application/json; charset=utf-8')
    def review_decision(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>4096: raise ValueError('Invalid request size.')
            request=json.loads(self.rfile.read(length))
            result=review_changes.set_decision(request['batch_id'],request['entity_id'],request['decision'])
            self.send(200,json.dumps(result).encode(),'application/json')
        except (ValueError,KeyError,OSError,json.JSONDecodeError) as e: self.send(400,json.dumps({'error':str(e)}).encode(),'application/json')
    def model_selected(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            data=review_changes.latest()
            if not data or data['stale']: raise ValueError('The schedule changed; reassess before modeling.')
            selected=[x for x in data['items'] if x['decision']=='yes']
            if not selected: raise ValueError('Choose Yes model for at least one assessed change.')
            supported=[x for x in selected if ((x.get('assessment') or {}).get('interpretation',{}).get('change_type') in ('platform_change','stops_skipped','stops_added') or 'express to local' in (x['item'].get('alert_type') or '').lower())]
            blocked=[{'change_id':x['change_id'],'reason':auto_model.blocked_reason(x)} for x in selected if x not in supported]
            if not supported: raise ValueError('No selected changes can be drafted yet: '+', '.join(x['change_id'] for x in blocked)+'. Your selections remain saved.')
            if not LOCK.acquire(blocking=False): raise ValueError('Another feed operation is running.')
            try:
                result=platform_edit.create(data,supported,blocked=blocked)
            finally: LOCK.release()
            self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
        except (ValueError,OSError) as e: self.send(400,json.dumps({'error':str(e)}).encode(),'application/json')
    def start_model_job(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            data=review_changes.latest()
            if data and data['stale']: raise ValueError('The schedule changed; reassess before applying changes.')
            manual=manual_edits.status()['items']
            scope=pipeline_scope.summarize(data,manual)
            selected=[x for x in (data['items'] if data else []) if x['decision']=='yes']
            supported=scope['supported'];blocked=scope['blocked'];manual_selected=scope['manual'];missing=scope['missing']
            if missing: raise ValueError('These feed edits could not be matched to the loaded feed: '+', '.join(missing)+'. Remove or clarify them before applying.')
            pending=any(x['active']!=x['applied'] for x in manual)
            if not supported and not manual_selected and not pending: raise ValueError('No assessed, supported changes are ready to apply. Assess more alerts or add a feed edit.')
            if not LOCK.acquire(blocking=False): raise ValueError('Another feed operation is running.')
            job_id=uuid.uuid4().hex;total=len(supported)+len(manual_selected)
            job={'id':job_id,'state':'running','total':total,'selected_count':total,'blocked_changes':blocked,'events':[]}
            with MODEL_JOBS_LOCK: MODEL_JOBS[job_id]=job
            def progress(message,**details):
                with MODEL_JOBS_LOCK: job['events'].append({'message':message,**details})
            def worker():
                try:
                    changes=[];service_result=None
                    if supported:
                        service_result=platform_edit.create(data,supported,blocked=[],progress=progress)
                        changes.extend(x['change_id'] for x in service_result['changes'])
                    previous=manual_edits.status();items=copy.deepcopy(previous['items'])
                    for item in items:item['applied']=item['active']
                    manual_version=previous['version']
                    if manual_selected or pending:
                        progress('Preparing selected route metadata edits in routes.txt.')
                        base,base_hash,_=manual_edits.source()
                        manual_version=manual_edits.build(base,base_hash,items)
                        changes.extend(x['id'] for x in manual_selected)
                    progress('Building and validating the combined GTFS output.')
                    output=pipeline_output.create(service_result,items,changes,progress=progress,publish=False,excluded_alerts=blocked)
                    if manual_selected or pending:
                        try:
                            manual_edits.save(items,output['base_sha256'],manual_version)
                            pipeline_output.commit(output)
                        except Exception:
                            manual_edits.save(previous['items'],output['base_sha256'],previous['version'])
                            raise
                    else: pipeline_output.commit(output)
                    with MODEL_JOBS_LOCK: job['state']='completed';job['changes']=changes;job['output_sha256']=output['sha256']
                except Exception as e:
                    with MODEL_JOBS_LOCK: job['state']='failed';job['error']=str(e)
                finally: LOCK.release()
            try: threading.Thread(target=worker,daemon=True).start()
            except Exception:
                LOCK.release()
                with MODEL_JOBS_LOCK: MODEL_JOBS.pop(job_id,None)
                raise
            self.send(200,json.dumps({'id':job_id}).encode(),'application/json')
        except (ValueError,OSError) as e: self.send(400,json.dumps({'error':str(e)}).encode(),'application/json')
    def reassess_alerts(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            latest=assessment_queue.latest()
            if not latest: raise ValueError('No alert snapshot is available.')
            batch=assessment_queue.create(latest['snapshot_id'])
            self.send(200,json.dumps({'batch_id':batch}).encode(),'application/json')
        except (ValueError,OSError) as e:
            self.send(400,json.dumps({'error':str(e)}).encode(),'application/json')
    def analyze_alert(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>200000: raise ValueError('Alert input is too large.')
            request=json.loads(self.rfile.read(length)); text=request.get('text','').strip()
            if not text: raise ValueError('Alert text is empty.')
            if realtime_alerts.classify(text)=='no_schedule_modeling_needed':
                result={'id':request.get('id') or 'facility-notice','text':text,'source':request.get('source','pasted text'),'status':'no_schedule_modeling_needed','interpretation':{'reason':'This notice concerns station access or equipment and does not describe a schedule change.'},'plan':[]}
                return self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            if not ASSISTANT_LOCK.acquire(blocking=False): raise ValueError('The local assistant is already checking an alert; please wait.')
            try:
                result=assistant_bridge.interpret(text,request.get('source','pasted text'),service=self.ai_service())
            finally:
                ASSISTANT_LOCK.release()
            self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
        except (ValueError,OSError,json.JSONDecodeError) as e:
            self.send(400,json.dumps({'error':str(e)}).encode(),'application/json; charset=utf-8')
    def check_structured(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>50000: raise ValueError('Invalid request size.')
            request=json.loads(self.rfile.read(length))
            result=structured_checks.check_skips(**request)
            self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
        except (ValueError,TypeError,OSError,json.JSONDecodeError) as e:
            self.send(400,json.dumps({'error':str(e)}).encode(),'application/json; charset=utf-8')
    def manual_edits(self):
        if self.headers.get('Origin')!='http://127.0.0.1:8766': return self.send(403,b'Invalid origin','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>10000: raise ValueError('Invalid request size.')
            request=json.loads(self.rfile.read(length))
            with LOCK:
                result=manual_edits.add(request.get('text','').strip(),self.ai_service()) if self.path=='/api/manual-edits' else manual_edits.toggle(request.get('id'),request.get('active'))
            self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
        except (ValueError,TypeError,OSError,json.JSONDecodeError,zipfile.BadZipFile) as e:
            self.send(400,json.dumps({'error':str(e)}).encode(),'application/json; charset=utf-8')
    def do_GET(self):
        url=urllib.parse.urlsplit(self.path); q=urllib.parse.parse_qs(url.query)
        try:
            if url.path=='/download/pipeline':
                with LOCK:
                    result=pipeline_output.current()
                    if not result: raise ValueError('Run the pipeline to create an output feed.')
                    with open(result['source'],'rb') as f: content=f.read()
                self.send(200,content,'application/zip')
            elif url.path=='/api/pipeline-verification':
                with LOCK:
                    result=pipeline_output.current()
                    if not result:raise ValueError('Run the pipeline to compare its output.')
                    report=pipeline_output.verification(result)
                self.send(200,json.dumps(report,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/agency-presets':
                self.send(200,json.dumps({'items':agency_presets.list_all()},ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/pipeline-scope':
                data=review_changes.latest(); manual=manual_edits.status()['items']
                scope=pipeline_scope.summarize(data,manual)
                value={'ready_service':[x['change_id'] for x in scope['supported']],'ready_edits':[x['id'] for x in scope['manual'] if x['id'] not in scope['missing']],'reflected_edits':[x['id'] for x in scope['manual'] if x.get('reflection')=='reflected'],'blocked':scope['blocked'],'missing_edits':scope['missing'],'stale':scope['stale']}
                self.send(200,json.dumps(value,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/pipeline-output':
                with LOCK: result=pipeline_output.current()
                self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/draft-view':
                with LOCK: result=draft_view.current()
                self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/manual-edits':
                with LOCK: result=manual_edits.status()
                self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/download/manual-edits':
                with LOCK: content=manual_edits.download()
                self.send(200,content,'application/zip')
            elif url.path=='/ai_session.js':
                with open(os.path.join(workbench.ROOT,'ai_session.js'),'rb') as f:self.send(200,f.read(),'application/javascript; charset=utf-8')
            elif url.path=='/agency_presets.js':
                with open(os.path.join(workbench.ROOT,'agency_presets.js'),'rb') as f:self.send(200,f.read(),'application/javascript; charset=utf-8')
            elif url.path=='/manual_edits.js':
                with open(os.path.join(workbench.ROOT,'manual_edits.js'),'rb') as f:self.send(200,f.read(),'application/javascript; charset=utf-8')
            elif url.path=='/verification_view.js':
                with open(os.path.join(workbench.ROOT,'verification_view.js'),'rb') as f:self.send(200,f.read(),'application/javascript; charset=utf-8')
            elif url.path=='/pipeline_view.js':
                with open(os.path.join(workbench.ROOT,'pipeline_view.js'),'rb') as f:self.send(200,f.read(),'application/javascript; charset=utf-8')
            elif url.path=='/workflow_tracker.js':
                with open(os.path.join(workbench.ROOT,'workflow_tracker.js'),'rb') as f:self.send(200,f.read(),'application/javascript; charset=utf-8')
            elif url.path=='/schedule_compare.js':
                with open(os.path.join(workbench.ROOT,'schedule_compare.js'),'rb') as f:self.send(200,f.read(),'application/javascript; charset=utf-8')
            elif url.path=='/api/auto-model-status':
                self.send(200,json.dumps(auto_model.status(),ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/model-job':
                with MODEL_JOBS_LOCK:
                    job=MODEL_JOBS.get(q.get('id',[''])[0])
                    if not job: raise ValueError('Modeling job not found; it may have been cleared after a server restart.')
                    payload=json.dumps(job,ensure_ascii=False).encode()
                self.send(200,payload,'application/json; charset=utf-8')
            elif url.path=='/api/change-diagram':
                data=comparison_data.build(q['change_id'][0],q.get('date',[None])[0],q.get('direction',[None])[0])
                self.send(200,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/latest-platform-version':
                with LOCK:
                    output=pipeline_output.current()
                    if not output or not output.get('service'):raise ValueError('The completed output contains no service-change diagram.')
                    data=dict(output['service']);data['output']=output['source'];data['sha256']=output['sha256'];data['version']=output['version']
                self.send(200,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/download/platform-version':
                directory=os.path.join(workbench.DATA_DIR,'versions')
                paths=[os.path.join(directory,x) for x in os.listdir(directory) if x.startswith('platform-') and x.endswith('.json')]
                if not paths: raise ValueError('No platform draft exists yet.')
                with open(max(paths,key=os.path.getmtime),encoding='utf-8') as f: data=json.load(f)
                if data['base_sha256']!=assessment_queue.feed_hash(): raise ValueError('The draft belongs to a different base feed.')
                if not auto_model.is_current(data): raise ValueError('The selected changes or their assessments have changed; the draft is out of date.')
                import hashlib
                with open(data['output'],'rb') as f: content=f.read()
                if hashlib.sha256(content).hexdigest()!=data['sha256']: raise ValueError('Draft ZIP hash mismatch.')
                self.send(200,content,'application/zip')
            elif url.path=='/api/alert-assessments':
                self.send(200,json.dumps(review_changes.latest(),ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/summary':
                self.send(200,json.dumps(workbench.summary(),ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/latest-structured-check':
                import hashlib
                with open(os.path.join(workbench.DATA_DIR,'latest-structured-check.json'),encoding='utf-8') as f: data=json.load(f)
                with workbench.connect() as db: current=db.execute('select source from metadata').fetchone()[0]
                with open(current,'rb') as f: digest=hashlib.sha256(f.read()).hexdigest()
                if digest!=data['feed_sha256']: raise ValueError('The current feed differs from the checked feed.')
                self.send(200,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/scenario':
                path=os.path.join(workbench.DATA_DIR,'versions','financial-district-manifest.json')
                with open(path,encoding='utf-8') as f: data=json.load(f)
                with workbench.connect() as db: current=db.execute('select source from metadata').fetchone()[0]
                if os.path.realpath(data['source'])!=os.path.realpath(current): raise ValueError('This scenario was created from a different loaded feed.')
                data.pop('trip_id_mapping',None)
                self.send(200,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/download/scenario':
                path=os.path.join(workbench.DATA_DIR,'versions','financial-district-manifest.json')
                with open(path,encoding='utf-8') as f: data=json.load(f)
                with workbench.connect() as db: current=db.execute('select source from metadata').fetchone()[0]
                if os.path.realpath(data['source'])!=os.path.realpath(current): raise ValueError('This scenario was created from a different loaded feed.')
                import hashlib
                with open(data['output'],'rb') as f: content=f.read()
                if hashlib.sha256(content).hexdigest()!=data['sha256']: raise ValueError('Scenario ZIP hash mismatch.')
                self.send(200,content,'application/zip')
            elif url.path=='/api/routes':
                with LOCK:
                    draft=draft_view.current() if q.get('feed',[''])[0]=='draft' else None
                    if draft and not draft['available']: raise ValueError(draft['message'])
                    with workbench.connect(draft['db_path'] if draft else None) as db:
                        result=[dict(r) for r in db.execute('select * from routes order by route_short_name,route_id')]
                self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/api/diagram':
                with LOCK:
                    draft=draft_view.current() if q.get('feed',[''])[0]=='draft' else None
                    if draft and not draft['available']: raise ValueError(draft['message'])
                    result=diagram_data.build(q['route'][0],q['date'][0],db_path=draft['db_path'] if draft else None)
                self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            elif url.path=='/comparison_marey.js':
                with open(os.path.join(workbench.ROOT,'comparison_marey.js'),'rb') as f:self.send(200,f.read(),'application/javascript; charset=utf-8')
            elif url.path in ('/diagram.html','/comparison-mockup.html'):
                with open(os.path.join(workbench.ROOT,url.path.lstrip('/')),'rb') as f: self.send(200,f.read(),'text/html; charset=utf-8')
            else: self.send(404,b'Not found','text/plain')
        except (ValueError,KeyError,OSError) as e:
            self.send(400,json.dumps({'error':str(e)}).encode(),'application/json')
    def send(self,status,body,content_type):
        self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)

if __name__=='__main__':
    assessment_queue.resume_or_start_latest()
    print('Preview: http://127.0.0.1:8766/diagram.html',flush=True)
    http.server.ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
