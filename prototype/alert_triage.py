"""Conservative, source-aware triage for static-schedule modeling."""
import datetime as dt
import re


def classify(item, at=None):
    at=at or dt.datetime.now(dt.timezone.utc)
    now=at.timestamp()
    text=item.get('text','') or ''
    kind=(item.get('alert_type') or '').lower()
    source=(item.get('source') or '').lower()
    entity=item.get('id','') or ''
    periods=item.get('active_periods') or []
    future=any(p.get('start') is not None and p['start']>now for p in periods)
    planned=bool(re.search(r'\b(planned|scheduled|upcoming|starting tomorrow|beginning tomorrow)\b',kind+' '+text,re.I))
    # Only interpret MTA's entity prefix for the MTA feed, never other agencies.
    mta='api-endpoint.mta.info/' in source
    mta_planned=mta and entity.startswith('lmm:planned_work:')
    mta_incident=mta and entity.startswith('lmm:alert:')
    finite_long=any(p.get('end') is not None and p['end']>now+86400 for p in periods)
    starts=[p['start'] for p in periods if p.get('start') is not None and p['start']<=now]
    recent=bool(starts and max(starts)>=now-86400)
    incident_language=re.search(r'\b(delays?|delayed|mechanical problem|ems assistance|police (?:activity|investigation)|medical emergency|signal problem|incident|disruption)\b',text,re.I)
    if planned or mta_planned:
        return {'timing':'planned','recommendation':'assess_schedule','reason':'The source identifies planned or scheduled work; inspect any future service windows, including later recurrences.'}
    if future:
        return {'timing':'future_or_recurring','recommendation':'review','reason':'The source includes a future alert period. Its relationship to the service-impact window needs review.'}
    if (mta_incident or incident_language) and recent and not finite_long:
        return {'timing':'current_disruption','recommendation':'not_recommended','reason':'This appears to be a recent, unplanned disruption with no identified future recurrence; a static schedule edit is not recommended.'}
    return {'timing':'uncertain_or_ongoing','recommendation':'review','reason':'The duration or future service impact is unclear. Review before deciding whether to model it.'}
