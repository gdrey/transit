"""Rebuild selected supported changes locally after assessments or choices change."""
import copy,json,os,threading,time
import operation_lock,review_changes,platform_edit,workbench

_event=threading.Event();_guard=threading.Lock();_worker=None
_state={'state':'idle','message':'No automatic modeling has run yet.','events':[]}

def supported(x):
    return ((x.get('assessment') or {}).get('interpretation',{}).get('change_type') in ('platform_change','stops_skipped','stops_added') or 'express to local' in (x['item'].get('alert_type') or '').lower())

def selected(data):return [x for x in data['items'] if x.get('decision')=='yes']

def blocked_reason(x):
    pending=(x.get('assessment') or {}).get('draft_blockers') or []
    return ' '.join(pending) if pending else 'This change type cannot yet be written to GTFS.'

def is_current(manifest):
    data=review_changes.latest()
    if not data or data['stale'] or any(x['state'] in ('queued','running') for x in data['items']):return False
    chosen=selected(data)
    return bool(chosen) and manifest.get('selection_signature')==review_changes.selection_signature(data,chosen)

def _latest_manifest():
    directory=os.path.join(workbench.DATA_DIR,'versions')
    if not os.path.isdir(directory):return None
    paths=[os.path.join(directory,x) for x in os.listdir(directory) if x.startswith('platform-') and x.endswith('.json')]
    if not paths:return None
    with open(max(paths,key=os.path.getmtime),encoding='utf-8') as f:return json.load(f)

def status():
    with _guard:return copy.deepcopy(_state)

def _set(**values):
    with _guard:_state.update(values)

def request():
    global _worker
    with _guard:
        if _worker is None or not _worker.is_alive():
            _worker=threading.Thread(target=_run,daemon=True,name='automatic-modeling');_worker.start()
    _event.set()

def _run():
    while True:
        _event.wait();_event.clear()
        try:
            data=review_changes.latest()
            if not data or data['stale']:
                _set(state='idle',message='Load and assess a GTFS feed before modeling.',events=[]);continue
            if any(x['state'] in ('queued','running') for x in data['items']):
                _set(state='waiting',message='Waiting for the current assessments to finish.',events=[]);continue
            chosen=selected(data)
            if not chosen:
                _set(state='idle',message='No changes are selected for modeling.',events=[]);continue
            eligible=[x for x in chosen if supported(x)]
            blocked=[{'change_id':x['change_id'],'reason':blocked_reason(x)} for x in chosen if not supported(x)]
            if not eligible:
                _set(state='incomplete',message='None of the selected change types can be written to the draft yet.',blocked_changes=blocked,events=[]);continue
            existing=_latest_manifest()
            if existing and is_current(existing):
                _set(state='completed' if not blocked else 'incomplete',message='The draft reflects the current selected changes.' if not blocked else 'The draft excludes unsupported selected changes.',changes=[x['change_id'] for x in existing['changes']],version=existing['version'],blocked_changes=blocked,events=[]);continue
            if not operation_lock.LOCK.acquire(blocking=False):
                _set(state='waiting',message='Waiting for another feed operation to finish.',events=[])
                time.sleep(1);_event.set();continue
            try:
                # Re-read after acquiring the lock; a user decision may have changed.
                data=review_changes.latest();chosen=selected(data);eligible=[x for x in chosen if supported(x)];blocked=[{'change_id':x['change_id'],'reason':blocked_reason(x)} for x in chosen if not supported(x)]
                if data['stale'] or not eligible or any(x['state'] in ('queued','running') for x in data['items']):_event.set();continue
                _set(state='running',message='Modeling selected changes into a draft GTFS.',total=len(chosen),supported=len(eligible),blocked_changes=blocked,events=[])
                def progress(message,**details):
                    with _guard:_state['events'].append({'message':message,**details})
                result=platform_edit.create(data,eligible,blocked=blocked,progress=progress)
                if not is_current(result):
                    _set(state='waiting',message='Selections changed during modeling; rebuilding the draft.');_event.set();continue
                _set(state='completed' if not blocked else 'incomplete',message='All selected changes are included in the draft.' if not blocked else 'The draft excludes unsupported selected changes.',changes=[x['change_id'] for x in result['changes']],version=result['version'],blocked_changes=blocked)
            finally:operation_lock.LOCK.release()
        except Exception as error:
            _set(state='failed',message=str(error) or type(error).__name__)
