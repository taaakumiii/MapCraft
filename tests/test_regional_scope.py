from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import national_backend as n
from regional_scope import PREFECTURES,REGIONS,selection,normalize,export_filename


class RegionalOutput(unittest.TestCase):
    def fixture(self,base):
        index=base/'data/japan.sqlite';index.parent.mkdir()
        poly=base/'japan.poly';poly.write_text('Japan\n1\n122 20\n154 20\n154 46\n122 46\n122 20\nEND\nEND\n')
        boxes={13:[(139.75,35.67,139.78,35.70),(142.,27.,142.01,27.01)],14:[(139.6,35.4,139.62,35.42)]}
        with closing(sqlite3.connect(index)) as db:
            db.executescript('CREATE TABLE meta(key TEXT,value TEXT);INSERT INTO meta VALUES("complete","1");'
                'CREATE TABLE features(data BLOB);CREATE TABLE bounds(id INTEGER);CREATE TABLE towns(data BLOB);CREATE TABLE prefectures(code TEXT,data BLOB)')
            for code in range(8,15):
                areas=boxes.get(code,[(139.+code*.001,36.,139.001+code*.001,36.001)])
                rings=[[[w,s],[e,s],[e,north],[w,north],[w,s]] for w,s,e,north in areas]
                # Broad marine admin coverage must not become a filled rectangle.
                admin={'outer':[[[138,26],[144,26],[144,37],[138,37],[138,26]]],'inner':[]}
                db.execute('INSERT INTO prefectures VALUES(?,?)',(f'JP-{code:02d}',n.packed(admin)))
                features=[{'type':'Feature','properties':{'N03_001':PREFECTURES[code]},'geometry':{'type':'Polygon','coordinates':[ring]}} for ring in rings]
                with zipfile.ZipFile(index.parent/f'N03-20260101_{code:02d}_GML.zip','w') as archive:
                    archive.writestr('land.geojson',json.dumps({'type':'FeatureCollection','features':features}))
            db.commit()
        return index,poly

    def create(self,base):
        index,poly=self.fixture(base);root=base/'project'
        config=n.create_project(root,index,1,poly,selection=selection('都道府県','東京都'),spawn=n.TOKYO_SPAWN)
        return root,config

    def test_catalogue_covers_47_prefectures_once_and_rejects_forged_codes(self):
        self.assertEqual(len(PREFECTURES),47)
        codes=[c for _,members in REGIONS.values() for c in members]
        self.assertEqual(sorted(codes),list(range(1,48)))
        self.assertEqual(selection('地方','関東')['codes'],list(range(8,15)))
        self.assertEqual(normalize({'kind':'prefecture','id':'pref-13','codes':[47]})['codes'],[13])
        with self.assertRaises(ValueError):normalize({'kind':'region','id':'../../escape'})

    def test_prefecture_keeps_islands_without_filling_intervening_sea(self):
        with tempfile.TemporaryDirectory() as folder:
            root,config=self.create(Path(folder))
            self.assertEqual(config['scope_mode'],'region');self.assertEqual(config['scope_selection']['codes'],[13])
            self.assertTrue(config['ocean']['enabled']);self.assertLess(config['total'],100)
            with closing(sqlite3.connect(root/'queue.sqlite')) as db:
                for lat,lon in [(35.681236,139.7658),(27.005,142.005)]:
                    x,z=n.forward(lat,lon,1)
                    self.assertIsNotNone(db.execute('SELECT 1 FROM tiles WHERE tx=? AND tz=?',(int(x//1024),int(z//1024))).fetchone())
                x,z=n.forward(31,141,1)
                self.assertIsNone(db.execute('SELECT 1 FROM tiles WHERE tx=? AND tz=?',(int(x//1024),int(z//1024))).fetchone())

    def test_expansion_reuses_grid_done_chunks_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root,config=self.create(Path(folder))
            with closing(sqlite3.connect(root/'queue.sqlite')) as db:
                tile=db.execute('SELECT tx,tz FROM tiles LIMIT 1').fetchone()
                db.execute('UPDATE tiles SET state="done",chunks=4096,seconds=12 WHERE tx=? AND tz=?',tile);db.commit()
            updated=n.extend_project(root,selection('地方','関東'))
            self.assertEqual(updated['map_id'],config['map_id']);self.assertEqual(updated['spawn'],list(config['spawn']))
            self.assertGreater(updated['total'],config['total'])
            self.assertEqual(n.extend_project(root,selection('地方','関東'))['total'],updated['total'])
            with closing(sqlite3.connect(root/'queue.sqlite')) as db:
                self.assertEqual(db.execute('SELECT state,chunks,seconds FROM tiles WHERE tx=? AND tz=?',tile).fetchone(),('done',4096,12))
            with self.assertRaises(ValueError):n.extend_project(root,selection('都道府県','神奈川県'))
            self.assertIn('関東',export_filename(updated))

    def test_build_rejects_other_project_and_does_not_export_partial_or_paused(self):
        with tempfile.TemporaryDirectory() as folder:
            root,config=self.create(Path(folder));request={'selection':selection('都道府県','東京都'),'scale':1,'destination':str(Path(folder)/'Tokyo.mcworld')}
            with patch.object(n,'run_tiles') as run,patch.object(n,'export_world') as export:
                with self.assertRaises(ValueError):n.build_selected(root,{**request,'selection':selection('都道府県','神奈川県')})
                run.assert_not_called()
                n.build_selected(root,request);export.assert_not_called()
                with closing(sqlite3.connect(root/'queue.sqlite')) as db:db.execute('UPDATE tiles SET state="done"');db.commit()
                n.build_selected(root,request);export.assert_called_once_with(root,request['destination'])
                export.reset_mock();(root/'pause.request').write_text('MapCraft GUI pause: test')
                n.build_selected(root,request);export.assert_not_called()

    def test_gui_does_not_clear_external_pause_or_take_over_managed_project(self):
        from national_state import project_busy
        with tempfile.TemporaryDirectory() as folder:
            root,config=self.create(Path(folder));pause=root/'pause.request'
            pause.write_text('MapCraft phase export: tokyo-prefecture',encoding='utf-8')
            request=Path(folder)/'request.json';request.write_text(json.dumps({'project':str(root),'gui':True}),encoding='utf-8')
            with n.project_lock(root):self.assertTrue(project_busy(root))
            self.assertFalse(project_busy(root))
            with patch.object(sys,'argv',['national_backend.py','run','--request',str(request)]),patch.object(n,'run_tiles') as run:
                with self.assertRaises(ValueError):n.main()
                run.assert_not_called()
                self.assertEqual(pause.read_text(encoding='utf-8'),'MapCraft phase export: tokyo-prefecture')
                config['active_phase']={'id':'managed','complete':True};n.atomic_json(root/'project.json',config)
                pause.write_text('MapCraft GUI pause: older',encoding='utf-8')
                with self.assertRaises(ValueError):n.main()
                self.assertEqual(pause.read_text(encoding='utf-8'),'MapCraft GUI pause: older')
                run.assert_not_called()


if __name__=='__main__':unittest.main()
