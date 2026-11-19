"""Exercise the cloud import in a transaction on a development branch."""
import io,os,sys,uuid,zipfile
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg,workspaces,cloud_import,cloud_schedule

data=io.BytesIO()
with zipfile.ZipFile(data,'w') as z:
    for name,content in {'agency.txt':'agency_name,agency_timezone\nTest,America/New_York\n','routes.txt':'route_id,route_type\nr,3\n','stops.txt':'stop_id,stop_name\ns,Test\n','trips.txt':'route_id,service_id,trip_id\nr,day,t\n','stop_times.txt':'trip_id,stop_id,stop_sequence,arrival_time\nt,s,1,25:01:00\n','calendar_dates.txt':'service_id,date,exception_type\nday,20261119,1\n'}.items(): z.writestr(name,content)
data.seek(0)
with psycopg.connect(sys.stdin.readline().strip(),connect_timeout=15) as conn:
    a,_=workspaces.new_workspace(conn); b,_=workspaces.new_workspace(conn)
    feed=uuid.uuid4(); result=cloud_import.import_feed(conn,a,feed,'test/object.zip','test',data)
    assert result['counts']['stop_times.txt']==1
    assert cloud_schedule.routes(conn,a,feed)[0]['route_id']=='r'
    assert cloud_schedule.trips_on_date(conn,a,feed,'r','2026-11-19')['trips'][0]['trip_id']=='t'
    assert cloud_schedule.trips_on_date(conn,b,feed,'r','2026-11-19')['trips']==[]
    assert cloud_schedule.stop_times(conn,a,feed,'t')[0]['arrival_time']=='25:01:00'
    with conn.cursor() as cur:
        cur.execute("SELECT row_data->>'arrival_time' FROM workbench.gtfs_rows WHERE workspace_id=%s AND feed_id=%s AND file_name='stop_times.txt'",(a,feed))
        assert cur.fetchone()[0]=='25:01:00'
        cur.execute('SELECT count(*) FROM workbench.gtfs_rows WHERE workspace_id=%s AND feed_id=%s',(b,feed))
        assert cur.fetchone()[0]==0
    conn.rollback()
print('Cloud import, after-midnight time and workspace scope passed')
