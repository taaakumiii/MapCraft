import importlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import leveldb
    import bathymetry_bulk as bulk
    from bathymetry_backend import apply_directory
    fixtures=importlib.import_module('test_bathymetry')
    NATIVE=True
except ImportError:NATIVE=False

def records(world):
    db=leveldb.LevelDB(str(Path(world)/'db'),create_if_missing=False)
    try:return dict(db.iterate())
    finally:db.close()

@unittest.skipUnless(NATIVE,'Native runtime required')
class BulkBathymetry(unittest.TestCase):
    def fixture(self,root):
        world=root/'world';world.mkdir(parents=True);helper=fixtures.Bathymetry();helper.world(world)
        db=leveldb.LevelDB(str(world/'db'),create_if_missing=False)
        db.put(b'unknown-global-record',b'keep actors, inventories and unknown data');db.close()
        return helper
    def run_bulk(self,root,helper,**kwargs):
        return bulk.apply_bulk(root,helper.reference(),root/'cache',{},lambda _:None,survey=helper.survey(),coverage='test-coast',expected_chunks=1,**kwargs)
    def test_bulk_equals_serial_and_keeps_all_old_records_in_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);helper=self.fixture(root/'serial');shutil.copytree(root/'serial',root/'bulk')
            before=records(root/'bulk/world')
            apply_directory(root/'serial/world',helper.reference(),root/'serial/cache',survey=helper.survey(),coverage='test-coast',state_path=root/'serial/bathymetry-state.sqlite',notify=lambda _:None)
            report=self.run_bulk(root/'bulk',helper)
            self.assertEqual(records(root/'serial/world'),records(root/'bulk/world'))
            self.assertEqual(report['storage_mode'],'verified_sorted_rebuild')
            backup=next((root/'bulk/backups').iterdir());db=leveldb.LevelDB(str(backup),create_if_missing=False)
            try:self.assertEqual(dict(db.iterate()),before)
            finally:db.close()
            with patch.object(bulk,'rebuilt_database') as rebuild:self.assertEqual(self.run_bulk(root/'bulk',helper),report)
            rebuild.assert_not_called()
    def test_failed_rebuild_does_not_publish_the_journal_or_change_the_world(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);helper=self.fixture(root);before=records(root/'world')
            with patch.object(bulk,'rebuilt_database',side_effect=InterruptedError('before install')):
                with self.assertRaises(InterruptedError):self.run_bulk(root,helper)
            self.assertEqual(records(root/'world'),before)
            self.assertFalse((root/'world/mapcraft_bathymetry.json').exists());self.assertFalse((root/'bathymetry-state.sqlite').exists())
    def test_resume_after_swap_finishes_the_journal_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);helper=self.fixture(root)
            with patch.object(bulk,'publish',side_effect=InterruptedError('after install')):
                with self.assertRaises(InterruptedError):self.run_bulk(root,helper)
            before=records(root/'world')
            with patch.object(bulk,'rebuilt_database') as rebuild:report=self.run_bulk(root,helper)
            rebuild.assert_not_called();self.assertEqual(records(root/'world'),before)
            self.assertTrue((root/'bathymetry-state.sqlite').exists());self.assertEqual(report['coverage'],'test-coast')
    def test_missing_chunks_refuse_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);helper=self.fixture(root);before=records(root/'world')
            with self.assertRaisesRegex(ValueError,'チャンク数'):
                bulk.apply_bulk(root,helper.reference(),root/'cache',{},lambda _:None,survey=helper.survey(),coverage='test-coast',expected_chunks=2)
            self.assertEqual(records(root/'world'),before);self.assertFalse((root/'world/mapcraft_bathymetry.json').exists())

if __name__=='__main__':unittest.main()
