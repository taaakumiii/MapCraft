from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import managed_control as c


class ManagedControls(unittest.TestCase):
    def fixture(self,folder):
        base=Path(folder);root=base/'worlds/test';root.mkdir(parents=True);control=base/'自動生成';control.mkdir()
        runtime=base/'engine/national-runtime/python.exe';runtime.parent.mkdir(parents=True);runtime.touch()
        program=base/'worker.py';program.write_text('# test only')
        c.write(root/'project.json',{'format':1,'map_id':'stable-map','origin':[35.,135.],'total':3,'world_name':'Test'})
        with closing(sqlite3.connect(root/'queue.sqlite')) as db:
            db.execute('CREATE TABLE tiles(state TEXT)');db.executemany('INSERT INTO tiles VALUES(?)',[('done',),('done',),('pending',)]);db.commit()
        with closing(sqlite3.connect(root/'atlas.sqlite')) as db:
            db.execute('CREATE TABLE rendered(style INTEGER)');db.execute('INSERT INTO rendered VALUES(2)');db.commit()
        task={'id':'worker','title':'Test worker','runtime':str(runtime),'program':str(program),'args':[],
              'state_file':'test-state.json','pid':1,'creation_ticks':1}
        record={'schema':1,'base':str(base),'project':str(root),'title':'Test project','map_id':'stable-map','status':'running','tasks':[task]}
        c.write(c.manifest_path(base),record);c.write(control/'test-state.json',{'status':'paused','updated':c.now()})
        return base,root,record

    def test_status_is_read_only_and_reports_actual_saved_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            base,root,record=self.fixture(folder);before={p:p.read_bytes() for p in root.iterdir() if p.is_file()}
            with patch.object(c,'process_identity',return_value=None):result=c.snapshot(base)
            self.assertEqual(result['terrain'],{'total':3,'completed':2});self.assertEqual(result['navigation'],{'total':2,'completed':1})
            self.assertTrue(result['safe_to_shutdown']);self.assertTrue(result['can_resume'])
            self.assertEqual(before,{p:p.read_bytes() for p in root.iterdir() if p.is_file()})

    def test_pause_and_resume_restore_phase_marker_and_prevent_duplicate_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            base,root,record=self.fixture(folder);marker=root/'pause.request';marker.write_text('MapCraft phase export: test')
            identity=lambda pid: {'creation_ticks':2,'image':record['tasks'][0]['runtime']} if pid==999 else None
            with patch.object(c,'process_identity',side_effect=identity),patch.object(c.subprocess,'Popen',return_value=Mock(pid=999)) as launch:
                self.assertTrue(c.pause(base)['safe_to_shutdown'])
                with patch.dict(os.environ,{'MAPCRAFT_CONTROL_BASE':str(base),'MAPCRAFT_JOB_ID':'current'}):
                    with self.assertRaises(InterruptedError):c.checkpoint(root)
                    c.checkpoint(base/'another-project')
                result=c.resume(base);self.assertEqual(result['status'],'running');self.assertFalse(result['safe_to_shutdown'])
                self.assertEqual(marker.read_text(),'MapCraft phase export: test');self.assertFalse(c.pause_path(base).exists())
                with self.assertRaises(RuntimeError):c.resume(base)
                self.assertEqual(launch.call_count,1)

    def test_changed_world_and_foreign_pause_never_start_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            base,root,record=self.fixture(folder)
            with patch.object(c,'process_identity',return_value=None),patch.object(c.subprocess,'Popen') as launch:
                c.pause(base);marker=root/'pause.request';marker.write_text('Another owner')
                with self.assertRaises(RuntimeError):c.resume(base)
                self.assertEqual(marker.read_text(),'Another owner');self.assertTrue(c.pause_path(base).exists())
                c.write(root/'project.json',{'map_id':'changed'})
                with self.assertRaises(ValueError):c.resume(base)
                launch.assert_not_called()

    def test_reused_pid_is_not_treated_as_our_live_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            base,root,record=self.fixture(folder)
            with patch.object(c,'process_identity',return_value={'creation_ticks':99,'image':record['tasks'][0]['runtime']}):
                self.assertFalse(c.alive(record['tasks'][0]));self.assertTrue(c.snapshot(base)['can_resume'])

    def test_resume_skips_completed_predecessor(self):
        with tempfile.TemporaryDirectory() as folder:
            base,root,record=self.fixture(folder)
            task={**record['tasks'][0],'id':'waiting','state_file':'next-state.json'};record['tasks'].append(task)
            c.write(c.manifest_path(base),record);c.write(base/'自動生成/test-state.json',{'status':'complete'})
            c.write(base/'自動生成/next-state.json',{'status':'paused'})
            identity=lambda pid: {'creation_ticks':2,'image':task['runtime']} if pid==999 else None
            with patch.object(c,'process_identity',side_effect=identity),patch.object(c.subprocess,'Popen',return_value=Mock(pid=999)) as launch:
                c.pause(base);c.resume(base);self.assertEqual(launch.call_count,1)
                saved=c.read(c.manifest_path(base));self.assertEqual(saved['tasks'][0]['pid'],1);self.assertEqual(saved['tasks'][1]['pid'],999)

    def test_reboot_during_export_resumes_without_deleting_phase_barrier(self):
        with tempfile.TemporaryDirectory() as folder:
            base,root,record=self.fixture(folder)
            config=c.read(root/'project.json');config['active_phase']={'id':'tokyo','complete':True};c.write(root/'project.json',config)
            record['tasks'][0]['phase']='tokyo';record['pause_marker']='MapCraft managed pause: obsolete'
            record['previous_pause_marker']='MapCraft phase export: tokyo';c.write(c.manifest_path(base),record)
            c.write(base/'自動生成/test-state.json',{'status':'running'})
            marker=root/'pause.request';marker.write_text('MapCraft phase export: tokyo');before=marker.read_bytes()
            identity=lambda pid: {'creation_ticks':2,'image':record['tasks'][0]['runtime']} if pid==999 else None
            with patch.object(c,'process_identity',side_effect=identity),patch.object(c.subprocess,'Popen',return_value=Mock(pid=999)) as launch:
                self.assertEqual(c.snapshot(base)['tasks'][0]['status'],'interrupted')
                result=c.resume(base);self.assertEqual(result['status'],'running');self.assertEqual(launch.call_count,1)
                self.assertEqual(marker.read_bytes(),before)

    def test_different_region_barrier_and_active_writer_still_block_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            base,root,record=self.fixture(folder)
            config=c.read(root/'project.json');config['active_phase']={'id':'tokyo'};c.write(root/'project.json',config)
            record['tasks'][0]['phase']='tokyo';c.write(c.manifest_path(base),record)
            marker=root/'pause.request';marker.write_text('MapCraft phase export: another-region')
            with patch.object(c,'process_identity',return_value=None),patch.object(c.subprocess,'Popen') as launch:
                with self.assertRaises(RuntimeError):c.resume(base)
                self.assertEqual(marker.read_text(),'MapCraft phase export: another-region')
                marker.write_text('MapCraft phase export: tokyo')
                with patch.object(c,'locks_free',return_value=False):
                    with self.assertRaises(RuntimeError):c.resume(base)
                launch.assert_not_called()

    def test_reboot_after_handoff_uses_new_phase_and_skips_finished_predecessor(self):
        with tempfile.TemporaryDirectory() as folder:
            base,root,record=self.fixture(folder)
            config=c.read(root/'project.json');config['active_phase']={'id':'kanto'};c.write(root/'project.json',config)
            record['tasks'][0]['phase']='tokyo';record['previous_pause_marker']='MapCraft phase export: tokyo'
            second={**record['tasks'][0],'id':'kanto','phase':'kanto','state_file':'next-state.json'}
            record['tasks'].append(second);c.write(c.manifest_path(base),record)
            c.write(base/'自動生成/test-state.json',{'status':'complete'});c.write(base/'自動生成/next-state.json',{'status':'running'})
            marker=root/'pause.request';marker.write_text('MapCraft phase export: kanto')
            identity=lambda pid: {'creation_ticks':2,'image':second['runtime']} if pid==999 else None
            with patch.object(c,'process_identity',side_effect=identity),patch.object(c.subprocess,'Popen',return_value=Mock(pid=999)) as launch:
                c.resume(base);self.assertEqual(launch.call_count,1);self.assertEqual(marker.read_text(),'MapCraft phase export: kanto')

    def test_normal_pause_is_a_reason_not_a_generation_error(self):
        with tempfile.TemporaryDirectory() as folder:
            base,root,record=self.fixture(folder)
            reason='一時停止しました。MapCraftから再開できます。'
            c.write(base/'自動生成/test-state.json',{'status':'paused','error':reason})
            with patch.object(c,'process_identity',return_value=None):task=c.snapshot(base)['tasks'][0]
            self.assertIsNone(task['error']);self.assertEqual(task['stop_reason'],reason)
            c.write(base/'自動生成/test-state.json',{'status':'running','error':reason})
            with patch.object(c,'process_identity',return_value=None):self.assertIsNone(c.snapshot(base)['tasks'][0]['error'])


if __name__=='__main__':unittest.main()
