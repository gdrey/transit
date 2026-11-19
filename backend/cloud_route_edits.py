"""Deterministic route metadata edits on the selected original GTFS archive."""
import csv,hashlib,io,re,zipfile

FIELDS={'route_color','route_short_name'}

def apply(original,operations):
    if not isinstance(operations,list) or len(operations)>10000: raise ValueError('Invalid operations')
    changes=[]; data=io.BytesIO(original)
    with zipfile.ZipFile(data) as source:
        files=[i for i in source.infolist() if i.filename.lower()=='routes.txt']
        if len(files)!=1: raise ValueError('Expected one routes.txt')
        with source.open(files[0]) as raw:
            reader=csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))
            columns=reader.fieldnames
            if not columns or 'route_id' not in columns or len(columns)!=len(set(columns)): raise ValueError('Invalid routes.txt')
            rows=list(reader)
        by_id={r['route_id']:r for r in rows}
        if len(by_id)!=len(rows):raise ValueError('Duplicate route IDs')
        touched=set()
        for op in operations:
            if not isinstance(op,dict) or set(op)!={'route_id','field','value'}:raise ValueError('Invalid operation')
            route,field,value=op['route_id'],op['field'],op['value']
            if not isinstance(route,str) or route not in by_id or field not in FIELDS or not isinstance(value,str):raise ValueError('Invalid route edit')
            if (route,field) in touched:raise ValueError('Conflicting edits on the same route field')
            touched.add((route,field))
            if field=='route_color':
                value=value.lstrip('#').upper()
                if not re.fullmatch('[0-9A-F]{6}',value):raise ValueError('Invalid route color')
            elif not value or len(value)>255:raise ValueError('Invalid route short name')
            if field not in columns:columns.append(field)
            before=by_id[route].get(field,'') or ''
            by_id[route][field]=value
            changes.append({'route_id':route,'field':field,'before':before,'after':value})
        csvout=io.StringIO(newline='');writer=csv.DictWriter(csvout,fieldnames=columns,lineterminator='\n');writer.writeheader();writer.writerows(rows)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as dest:
            for info in source.infolist():dest.writestr(info,csvout.getvalue().encode('utf-8') if info.filename==files[0].filename else source.read(info))
    output=out.getvalue()
    # Read the exact ZIP that will be stored for download, not intermediate rows.
    with zipfile.ZipFile(io.BytesIO(output)) as verified:
        if verified.testzip() is not None:raise ValueError('Output ZIP verification failed')
        final={r['route_id']:r for r in csv.DictReader(io.TextIOWrapper(verified.open(files[0].filename),encoding='utf-8-sig',newline=''))}
        if any(final[c['route_id']].get(c['field'])!=c['after'] for c in changes):raise ValueError('Output verification failed')
    return output,{'original_sha256':hashlib.sha256(original).hexdigest(),'output_sha256':hashlib.sha256(output).hexdigest(),'changes':changes}
