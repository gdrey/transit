"""Read-only comparison for platform boarding changes resolved to feed IDs."""
import structured_checks
import workbench


def check(route_id, direction_id, stop_ids, target_direction_id, target_stop_ids, timezone, windows):
    if direction_id not in ('0','1') or target_direction_id not in ('0','1') or direction_id==target_direction_id:
        raise ValueError('Resolve distinct source and target platform travel directions from the feed.')
    if not isinstance(target_stop_ids,list) or len(target_stop_ids)!=len(stop_ids) or len(set(target_stop_ids))!=len(target_stop_ids):
        raise ValueError('Provide one distinct target platform for each current platform.')
    mappings=[]
    with workbench.connect() as db:
        for original,target in zip(stop_ids,target_stop_ids):
            a=db.execute('select stop_id,stop_name,parent_station from stops where stop_id=?',(original,)).fetchone()
            b=db.execute('select stop_id,stop_name,parent_station from stops where stop_id=?',(target,)).fetchone()
            if not a or not b or not a['parent_station'] or a['parent_station']!=b['parent_station'] or original==target:
                raise ValueError('Platform pair '+original+' → '+target+' must be distinct stops at the same parent station.')
            served=db.execute('''select count(*) from trips t join stop_times st on st.trip_id=t.trip_id where t.route_id=? and t.direction_id=? and st.stop_id=?''',(route_id,target_direction_id,target)).fetchone()[0]
            if not served: raise ValueError('Target platform '+target+' is not used by the specified target direction on this route.')
            mappings.append({'station_id':a['parent_station'],'station_name':a['stop_name'],'current_stop_id':original,'target_stop_id':target})
    base=structured_checks.check_skips('stops_skipped',route_id,direction_id,stop_ids,timezone,windows,require_route_stops=False)
    counts=base['by_stop']
    for mapping in mappings: mapping['conflicting_stop_records']=counts[mapping['current_stop_id']]
    evidence=None
    if not base['contradicting_stop_records'] and not base['missing_time_records'] and not base['frequency_templates_excluded']:
        try:evidence=structured_checks.check_skips('stops_skipped',route_id,direction_id,target_stop_ids,timezone,windows)
        except ValueError:pass
    reflected=bool(evidence and not evidence['missing_time_records'] and not evidence['frequency_templates_excluded'] and all(set(target_stop_ids)<=set(x) for x in evidence['served_stops_by_window']))
    return {'status':'likely_not_modeled' if base['contradicting_stop_records'] else ('already_modeled' if reflected else 'inconclusive'),
            'route_id':route_id,'direction_id':direction_id,'target_direction_id':target_direction_id,
            'mappings':mappings,'target_evidence':evidence,'windows':windows,'window_counts':base['window_counts'],
            'conflicting_stop_records':base['contradicting_stop_records'],'sample_records':base['sample_records'],
            'missing_time_records':base['missing_time_records'],'frequency_templates_excluded':base['frequency_templates_excluded'],
            'limitations':['The intended platform direction is inferred from trip headsigns and stop usage; confirm that it identifies the platform named by the alert.',
                           'Only specified route, direction, stations and alert windows are checked; trips on other routes or directions may also be affected.',
                           'Arrival time selects affected stop records; boundary treatment and trips with missing times require review.',
                           'A reflected result requires target-platform stop records in every alert window and for every named station; missing times and frequency trips prevent confirmation.',
                           'A platform stop ID does not by itself establish track geometry, accessibility, transfers, or operational feasibility.']}
