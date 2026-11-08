from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import national_backend
import jobcache

class AtomicProgress(unittest.TestCase):
    def test_transient_windows_reader_lock_retries_without_losing_the_json(self):
        for module in (national_backend,jobcache):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'progress.json';path.write_text('{"old":true}')
                replace=os.replace;attempts=[]
                def transient(source,target):
                    attempts.append(source)
                    if len(attempts)<4:raise PermissionError('Windows reader still has the file open')
                    return replace(source,target)
                with patch.object(module.os,'replace',transient):module.atomic_json(path,{'complete':32})
                self.assertEqual(json.loads(path.read_text()),{'complete':32});self.assertEqual(len(attempts),4)
                self.assertEqual(list(Path(directory).iterdir()),[path])
    def test_concurrent_progress_writers_use_distinct_temporary_files(self):
        for module in (national_backend,jobcache):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'progress.json'
                with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda i:module.atomic_json(path,{'value':i}),range(32)))
                self.assertIn(json.loads(path.read_text())['value'],range(32));self.assertEqual(list(Path(directory).iterdir()),[path])

if __name__=='__main__':unittest.main()
