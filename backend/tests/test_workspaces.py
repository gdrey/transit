import unittest
from unittest.mock import MagicMock
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workspaces

class WorkspaceTests(unittest.TestCase):
    def test_workspace_token_is_only_stored_as_hash(self):
        conn=MagicMock(); workspace,token=workspaces.new_workspace(conn)
        args=conn.cursor.return_value.__enter__.return_value.execute.call_args.args[1]
        self.assertNotIn(token,str(args))
        self.assertEqual(args[1],workspaces.hashlib.sha256(token.encode('ascii')).digest())
        self.assertEqual(len(token),43)
    def test_feed_lookup_always_scopes_by_workspace(self):
        conn=MagicMock(); cur=conn.cursor.return_value.__enter__.return_value; cur.fetchone.return_value=None
        with self.assertRaises(workspaces.NotFound): workspaces.get_feed(conn,'workspace-a','feed-b')
        self.assertEqual(cur.execute.call_args.args[1],('workspace-a','feed-b'))
        self.assertIn('workspace_id=%s',cur.execute.call_args.args[0])
    def test_invalid_workspace_or_token_is_rejected(self):
        self.assertFalse(workspaces.authenticate(MagicMock(),'bad','token'))

if __name__=='__main__': unittest.main()
