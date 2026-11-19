"""Private, workspace-scoped persistence primitives for the hosted workbench.

This module is server-side only. The caller owns request authentication,
limits, CSRF protections and connection lifecycle; it is not an HTTP server.
"""
import hashlib
import secrets
import uuid

class NotFound(Exception):
    """An identifier does not exist in this workspace."""

def new_workspace(conn):
    workspace = uuid.uuid4()
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode('ascii')).digest()
    with conn.cursor() as cur:
        cur.execute('INSERT INTO workbench.workspaces (id,token_hash) VALUES (%s,%s)', (workspace,digest))
    return str(workspace),token

def authenticate(conn, workspace, token):
    try:
        workspace = uuid.UUID(workspace)
        if not isinstance(token,str) or len(token)>256: return False
        digest = hashlib.sha256(token.encode('utf-8')).digest()
    except (ValueError,TypeError,UnicodeError): return False
    with conn.cursor() as cur:
        cur.execute('SELECT 1 FROM workbench.workspaces WHERE id=%s AND token_hash=%s',(workspace,digest))
        return cur.fetchone() is not None

def save_feed(conn, workspace, archive, source_name):
    # Call only after authenticating the workspace and validating the ZIP.
    feed=uuid.uuid4(); digest=hashlib.sha256(archive).hexdigest()
    with conn.cursor() as cur:
        cur.execute('INSERT INTO workbench.feeds (id,workspace_id,sha256,source_name,original_zip) VALUES (%s,%s,%s,%s,%s)', (feed,workspace,digest,source_name,archive))
    return str(feed),digest

def get_feed(conn,workspace,feed):
    with conn.cursor() as cur:
        cur.execute('SELECT sha256,source_name,original_zip FROM workbench.feeds WHERE workspace_id=%s AND id=%s',(workspace,feed))
        row=cur.fetchone()
    if not row: raise NotFound('Feed not found')
    return row
