from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts import continue_region as c

class ContinueRegion(unittest.TestCase):
    def fixture(self,folder):
        base=Path(folder);root=base/'worlds/project';(root/'world').mkdir(parents=True);control=base/'自動生成';control.mkdir();(base/'exports').mkdir()
        config={'format':1,'map_id':'same-map','projection':'national_mercator','origin':[35.,135.],'scale':1.,'tile':1024,'spawn':[35.681236,139.7658],'total':3}
        c.n.atomic_json(root/'project.json',config)
        with closing(sqlite3.connect(root/'queue.sqlite')) as db:
            db.execute('CREATE TABLE tiles(tx INTEGER,tz INTEGER,state TEXT,chunks INTEGER DEFAULT 0,seconds REAL DEFAULT 0,PRIMARY KEY(tx,tz))')
            db.executemany('INSERT INTO tiles(tx,tz,state) VALUES(?,0,?)',[(0,'done'),(1,'done'),(2,'deferred_phase')]);db.commit()
        plan={k:config[k] for k in ('map_id','projection','origin','scale','tile')};plan.update(total=3,additional=1,tiles=[[0,0],[1,0],[2,0]],scope='test extension')
        plan_path=base/'plan.json';c.n.atomic_json(plan_path,plan)
        settings=base/'settings.json';c.n.atomic_json(settings,{'base':str(base),'project':str(root),'budget_bytes':10**15})
        request={k:config[k] for k in ('map_id','projection','origin','scale','tile')}
        request.update(request_id='test-continuation',base=str(base),project=str(root),snapshot=str(base/'完成ワールド/wards.mcworld'),
            from_phase='wards',from_completed=2,phase='prefecture',title='Full prefecture',filename='full.mcworld',
            settings=str(settings),plan=str(plan_path),plan_sha256=c.digest(plan_path),budget_bytes=10**15,workers=2,
            wait_states=['finished.json'],mobs={'wildlife_enabled':True,'bosses_enabled':True,'boss_beacons_enabled':True},villagers={'enabled':True},bathymetry={'enabled':True})
        output=base/'exports/wards.mcworld';self.archive(output,request)
        main={'phase':'wards','status':'complete','postprocess_phase':'native_ecology','output':str(output),'progress':{'completed':2,'total':2}}
        c.n.atomic_json(control/'進捗.json',main);c.n.atomic_json(control/'finished.json',{'status':'complete'})
        return request,root,main
    def archive(self,path,request,beacons=True):
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('mapcraft_national.json',json.dumps({'project':{'map_id':request['map_id']},'progress':{'completed':2}}))
            archive.writestr('mapcraft_phase.json',json.dumps({'id':'wards','complete':True}))
            archive.writestr('mapcraft_ecology.json',json.dumps({'options':{**request['mobs'],'boss_beacons_enabled':beacons},'runtime_pack':False}))
            archive.writestr('mapcraft_villagers.json',json.dumps({'options':request['villagers']}));archive.writestr('mapcraft_bathymetry.json',json.dumps({'options':request['bathymetry']}))
            archive.writestr('level.dat',b'example');archive.writestr('db/CURRENT',b'example database bytes')
    def test_waits_for_all_final_features_without_changing_the_project(self):
        with tempfile.TemporaryDirectory() as folder:
            request,root,main=self.fixture(folder);main['status']='running';c.n.atomic_json(Path(folder)/'自動生成/進捗.json',main)
            before=(root/'project.json').read_bytes();job=c.Continuation(request);job.run(once=True)
            self.assertEqual((root/'project.json').read_bytes(),before);self.assertFalse(Path(request['snapshot']).exists())
            self.assertEqual(job.state['status'],'waiting')
    def test_incomplete_beacon_export_never_starts_expansion(self):
        with tempfile.TemporaryDirectory() as folder:
            request,root,main=self.fixture(folder);self.archive(main['output'],request,beacons=False);job=c.Continuation(request)
            with patch.object(c.PhaseFinish,'generate') as generate:
                with self.assertRaises(ValueError):job.transition(main)
                generate.assert_not_called()
            with closing(sqlite3.connect(root/'queue.sqlite')) as db:self.assertEqual(db.execute('SELECT state FROM tiles WHERE tx=2').fetchone()[0],'deferred_phase')
            self.assertFalse(Path(request['snapshot']).exists())
    def test_verified_snapshot_precedes_extension_and_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            request,root,main=self.fixture(folder);destination=Path(request['snapshot']);destination.parent.mkdir();destination.write_bytes(b'keep this independent file')
            (root/'pause.request').write_text('MapCraft phase export: wards')
            original=c.digest(main['output']);job=c.Continuation(request)
            request['ocean']={'enabled':True,'depth':60}
            def generate(phase,workers):
                frozen=Path(job.state['snapshot']['path']);self.assertNotEqual(frozen,destination)
                self.assertEqual(c.digest(frozen),original);self.assertEqual(destination.read_bytes(),b'keep this independent file')
                self.assertEqual(c.n.load_project(root)['map_id'],request['map_id'])
                self.assertEqual(c.n.load_project(root)['ocean'],{'enabled':True,'depth':60})
                with zipfile.ZipFile(frozen) as archive:self.assertNotIn('ocean',json.loads(archive.read('mapcraft_national.json'))['project'])
                with closing(sqlite3.connect(root/'queue.sqlite')) as db:self.assertEqual(db.execute('SELECT tx,state FROM tiles ORDER BY tx').fetchall(),[(0,'done'),(1,'done'),(2,'pending')])
            with patch.object(c.PhaseFinish,'generate',generate):job.transition(main)
            self.assertEqual(job.state['status'],'complete');self.assertEqual(destination.read_bytes(),b'keep this independent file')

    def test_phase_export_mode_waits_for_final_output_and_predecessor(self):
        with tempfile.TemporaryDirectory() as folder:
            request,root,main=self.fixture(folder);request['completion_mode']='phase_export'
            main.pop('postprocess_phase');self.assertTrue(c.ready(request,main))
            main['status']='running';self.assertFalse(c.ready(request,main));main['status']='complete'
            main['progress']['completed']=1;self.assertFalse(c.ready(request,main));main['progress']['completed']=2
            c.n.atomic_json(Path(folder)/'自動生成/finished.json',{'status':'running'})
            self.assertFalse(c.ready(request,main))
            c.n.atomic_json(Path(folder)/'自動生成/finished.json',{'status':'complete'})
            c.n.atomic_json(Path(folder)/'自動生成/進捗.json',main)
            job=c.Continuation(request)
            with patch.object(c.PhaseFinish,'generate') as generate:job.run(once=True)
            generate.assert_called_once_with(2);self.assertTrue(Path(request['snapshot']).exists())

    def test_waiter_does_not_take_the_running_predecessors_controller_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            request,root,main=self.fixture(folder);main['status']='running'
            c.n.atomic_json(Path(folder)/'自動生成/進捗.json',main);before=(root/'project.json').read_bytes()
            with c.n.project_lock(Path(folder)/'自動生成/region-continuation-controller'):
                job=c.Continuation(request);job.run(once=True)
            self.assertEqual(job.state['status'],'waiting');self.assertEqual((root/'project.json').read_bytes(),before)

    def test_source_ocean_and_navigation_must_be_complete_before_freezing(self):
        with tempfile.TemporaryDirectory() as folder:
            request,root,main=self.fixture(folder)
            request.update(from_ocean={'enabled':True,'depth':60},require_navigation=True)
            with zipfile.ZipFile(main['output'],'a') as archive:
                archive.writestr('mapcraft_ocean.json',json.dumps({'options':request['from_ocean'],'runtime_pack':False}))
                for kind,name,identity in [('behavior_packs','world_behavior_packs.json','b'),('resource_packs','world_resource_packs.json','r')]:
                    archive.writestr(kind+'/mapcraft_national/manifest.json',json.dumps({'header':{'uuid':identity,'version':[0,19,4]}}))
                    archive.writestr(name,json.dumps([{'pack_id':identity,'version':[0,19,4]}]))
                archive.writestr('resource_packs/mapcraft_national/ui/hud_screen.json','{}')
                archive.writestr('behavior_packs/mapcraft_national/scripts/municipalities.js','export const municipalities=[];')
            c.verify_completed_archive(main['output'],request)
            request['from_ocean']['depth']=30
            with self.assertRaisesRegex(ValueError,'簡易海'):c.freeze_archive(main['output'],request['snapshot'],request)
            self.assertFalse(Path(request['snapshot']).exists())

if __name__=='__main__':unittest.main()
