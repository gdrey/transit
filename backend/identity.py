"""Workspace membership lookups after a trusted OAuth verifier checks a token."""
import uuid

class AccessDenied(Exception):pass

def require_workspace(conn,subject,workspace,write=False):
    if not isinstance(subject,str) or not subject or len(subject)>512:raise AccessDenied()
    try:workspace=uuid.UUID(str(workspace))
    except (ValueError,TypeError,AttributeError):raise AccessDenied()
    with conn.cursor() as cur:
        cur.execute('SELECT role FROM workbench.workspace_members WHERE workspace_id=%s AND subject=%s',(workspace,subject))
        row=cur.fetchone()
    if not row or (write and row[0] not in ('owner','editor')):raise AccessDenied()
    return workspace
