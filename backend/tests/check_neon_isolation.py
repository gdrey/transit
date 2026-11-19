"""Run against an isolated test branch only; connection string from stdin."""
import os,sys
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg
import workspaces

dsn=sys.stdin.readline().strip()
with psycopg.connect(dsn,connect_timeout=15) as conn:
    a,ta=workspaces.new_workspace(conn)
    b,tb=workspaces.new_workspace(conn)
    assert workspaces.authenticate(conn,a,ta)
    assert workspaces.authenticate(conn,b,tb)
    assert not workspaces.authenticate(conn,a,tb)
    feed,digest=workspaces.save_feed(conn,a,b'test archive bytes','test')
    assert bytes(workspaces.get_feed(conn,a,feed)[2])==b'test archive bytes'
    try: workspaces.get_feed(conn,b,feed)
    except workspaces.NotFound: pass
    else: raise AssertionError('Workspace isolation failed')
    # Roll back all test records without deleting existing records.
    conn.rollback()
print('Workspace authentication and feed isolation passed')
