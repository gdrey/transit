import gzip
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from backend import hosted_runtime

class SnapshotRestoreTest(unittest.TestCase):
    def test_database_reads_restore_complete_compressed_file(self):
        original = random.Random(1).randbytes(hosted_runtime.RESTORE_CHUNK+175)
        stored = gzip.compress(original)
        conn = MagicMock()
        def query(statement, parameters, **kwargs):
            result = MagicMock()
            if 'substring' in statement:
                offset, length, _, _ = parameters
                result.fetchone.return_value=(stored[offset-1:offset-1+length],)
            else:
                result.fetchall.return_value=[('feed.sqlite',len(stored))]
            return result
        conn.execute.side_effect=query
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            state = hosted_runtime.restore_files(conn,'test',path,'GET','/api/routes')
            self.assertEqual((path/'feed.sqlite').read_bytes(),original)
            self.assertEqual(set(state),{'feed.sqlite'})
        chunks = [call for call in conn.execute.call_args_list if 'substring' in call.args[0]]
        self.assertGreater(len(chunks),1)
        self.assertTrue(all(call.kwargs['binary'] for call in chunks))
