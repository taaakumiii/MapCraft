from dataclasses import replace
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image
from tracking import MapReference
from treasure import compile_treasure
from beds import compile_beds
from realmap import frame_bytes,hud_document
from national_state import index_info,project_info
from national_backend import forward,inverse

class GenerationContract(unittest.TestCase):
    def setUp(self):
        self.ref=MapReference('e5147427-4bb2-50c3-9185-53cecc856eaf',(35,135,35.01,135.01),1,1000,1000)
        elements=[]
        for i in range(100):
            x=(i%10)*.0008+135.0001;y=(i//10)*.0008+35.0001
            geometry=[{'lat':a,'lon':b} for a,b in [(y,x),(y,x+.0002),(y+.0002,x+.0002),(y+.0002,x),(y,x)]]
            elements.append({'type':'way','id':i,'tags':{'building':'house'},'geometry':geometry})
        self.payload={'elements':elements}
    def test_selection_and_furniture_independence(self):
        self.assertFalse(compile_treasure(replace(self.ref,treasure_enabled=False),self.payload)['plans'])
        self.assertFalse(compile_beds(replace(self.ref,beds_probability=0),self.payload)['plans'])
        all_boxes=compile_treasure(replace(self.ref,treasure_probability=1),self.payload)
        self.assertEqual(all_boxes['selected'],100)
        a=compile_beds(self.ref,self.payload)
        self.assertEqual(a,compile_beds(replace(self.ref,treasure_enabled=False),self.payload))
        self.assertGreater(a['selected'],0)
        self.assertEqual(compile_treasure(self.ref,self.payload),compile_treasure(self.ref,self.payload))
    def test_hud_aspect(self):
        self.assertEqual(Image.open(io.BytesIO(frame_bytes())).size,(54,74))
        for digits in (4,8):
            panel=hud_document(digits)['mapcraft_real']
            self.assertEqual(panel['anchor_from'],'top_right')
            frame=next(c['realmap_frame'] for c in panel['controls'] if 'realmap_frame' in c)
            self.assertFalse(frame['keep_ratio'])
    def test_projection(self):
        for lat,lon in [(35.681236,139.7658),(43.06,141.35),(33.59,130.40),(26.21,127.68)]:
            a,b=inverse(*forward(lat,lon,1),1)
            self.assertAlmostEqual(a,lat,places=9);self.assertAlmostEqual(b,lon,places=9)
    def test_missing_index_never_creates_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'absent.sqlite'
            with self.assertRaisesRegex(ValueError,'索引'):index_info(path)
            self.assertFalse(path.exists())
            with self.assertRaisesRegex(ValueError,'プロジェクト'):project_info(folder)
    def test_invalid_database_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'empty.sqlite'
            db=sqlite3.connect(path);db.close();before=path.read_bytes()
            with self.assertRaises(ValueError):index_info(path)
            self.assertEqual(before,path.read_bytes())

if __name__=='__main__':unittest.main()
