"""Stream a private GTFS object into a workspace-scoped Postgres index.

The caller must authorize the workspace and provide a private object-storage
reader. The caller is responsible for enforcing import quotas and job retries.
"""
import csv,hashlib,io,zipfile
from psycopg.types.json import Jsonb

MAX_FILES=2000
MAX_UNCOMPRESSED=1024*1024*1024
REQUIRED={'agency.txt':set(),'routes.txt':{'route_id'},'stops.txt':{'stop_id'},'trips.txt':{'route_id','service_id','trip_id'},'stop_times.txt':{'trip_id','stop_id','stop_sequence'}}
INDEXED=set(REQUIRED)|{'calendar.txt','calendar_dates.txt','shapes.txt','frequencies.txt'}

def import_feed(conn, workspace, feed, object_key, source_name, archive):
    """Import one selected feed atomically; the caller must commit the transaction."""
    archive.seek(0)
    digest=hashlib.sha256()
    for chunk in iter(lambda:archive.read(1024*1024),b''): digest.update(chunk)
    archive.seek(0)
    with zipfile.ZipFile(archive) as z:
        members={}
        for info in z.infolist():
            if '/' in info.filename or '\\' in info.filename: continue
            name=info.filename.lower()
            if name in members: raise ValueError('Duplicate file: '+name)
            members[name]=info
        if len(z.infolist())>MAX_FILES or sum(i.file_size for i in z.infolist())>MAX_UNCOMPRESSED:
            raise ValueError('Archive exceeds processing limits')
        if not REQUIRED.keys() <= members.keys(): raise ValueError('Missing required GTFS schedule files')
        if not {'calendar.txt','calendar_dates.txt'} & members.keys(): raise ValueError('Missing service calendar')
        with conn.cursor() as cur:
            cur.execute("INSERT INTO workbench.feed_versions (workspace_id,id,source_name,object_key,sha256,status) VALUES (%s,%s,%s,%s,%s,'importing')",(workspace,feed,source_name,object_key,digest.hexdigest()))
            counts={}
            for name in sorted(INDEXED & members.keys()):
                with z.open(members[name]) as raw:
                    reader=csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig',newline=''))
                    fields=reader.fieldnames
                    if not fields or len(fields)!=len(set(fields)) or not REQUIRED.get(name,set()) <= set(fields):
                        raise ValueError('Invalid headers in '+name)
                    count=0
                    with cur.copy('COPY workbench.gtfs_rows (workspace_id,feed_id,file_name,row_number,row_data) FROM STDIN') as copy:
                        for count,row in enumerate(reader,1):
                            if None in row: raise ValueError('Unexpected extra values in '+name)
                            copy.write_row((workspace,feed,name,count,Jsonb(row)))
                    counts[name]=count
            cur.execute("UPDATE workbench.feed_versions SET status='ready' WHERE workspace_id=%s AND id=%s",(workspace,feed))
    return {'feed_id':str(feed),'sha256':digest.hexdigest(),'counts':counts}
