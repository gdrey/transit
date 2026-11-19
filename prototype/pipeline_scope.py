"""Describe exactly which requests the next pipeline run can include."""
import auto_model

def summarize(data,manual):
    entries=(data or {}).get('items',[])
    selected=[x for x in entries if x.get('decision')=='yes']
    supported=[x for x in selected if auto_model.supported(x)]
    blocked=[]
    for x in entries:
        state=x['state'];status=(x.get('assessment') or {}).get('status')
        if state!='completed':
            reason={'not_checked':'Not assessed','cancelled':'Assessment cancelled','queued':'Assessment queued','running':'Assessment in progress','failed':'Assessment failed'}.get(state,'Assessment incomplete')
        elif status in ('needs_clarification','unsupported','inconclusive','assessment_failed'):
            reason={'needs_clarification':'Needs clarification','unsupported':'Change type needs assessment support','inconclusive':'GTFS assessment inconclusive','assessment_failed':'Assessment failed'}[status]
        elif x in selected and x not in supported:reason=auto_model.blocked_reason(x)
        else:continue
        blocked.append({'change_id':x['change_id'],'reason':reason})
    active=[x for x in manual if x.get('active')]
    missing=[x['id'] for x in active if x.get('reflection')=='unknown']
    return {'supported':supported,'blocked':blocked,'manual':active,'missing':missing,'stale':bool(data and data.get('stale'))}
