import sys,unittest,uuid
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import identity

class IdentityTests(unittest.TestCase):
    def test_viewer_cannot_write(self):
        conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchone.return_value=('viewer',)
        with self.assertRaises(identity.AccessDenied):identity.require_workspace(conn,'verified-subject',uuid.uuid4(),write=True)
    def test_nonmember_cannot_read(self):
        conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchone.return_value=None
        with self.assertRaises(identity.AccessDenied):identity.require_workspace(conn,'verified-subject',uuid.uuid4())
    def test_owner_can_write(self):
        conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchone.return_value=('owner',)
        workspace=uuid.uuid4()
        self.assertEqual(identity.require_workspace(conn,'verified-subject',workspace,write=True),workspace)
