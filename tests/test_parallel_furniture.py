import hashlib
import gc
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import amulet_nbt as nbt
    import leveldb
    import numpy as np
    from geology_backend import encode
    from furniture_backend import ensure_project_furniture
    from parallel_furniture import ensure_parallel_furniture,recover_swap,swap_database
    NATIVE=True
except ImportError:NATIVE=False

def fixture(root,tiles=4,side=2):
    root=Path(root);(root/'world/db').mkdir(parents=True);(root/'tile_data').mkdir()
    size=16*side
    config={'format':1,'map_id':'e5147427-4bb2-50c3-9185-53cecc856eaf','projection':'national_mercator','origin':[35.,135.],
        'scale':1.,'tile':size,'bbox':[34.9,135,35.1,136],'index':str(root/'unused.sqlite'),'treasure':{'enabled':True,'probability':1.},'beds':{'enabled':True,'probability':1.}}
    (root/'project.json').write_text(json.dumps(config),encoding='utf-8')
    db=leveldb.LevelDB(str(root/'world/db'),create_if_missing=True)
    palette=[nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag('minecraft:'+v),'states':nbt.CompoundTag({}),'version':nbt.IntTag(18168865)})) for v in ('air','oak_planks','stone')]
    blobs=[];xx,zz,yy=np.indices((16,16,16))
    for sy in range(2):
        y=yy+sy*16;indices=np.zeros((16,16,16),dtype=np.uint32)
        footprint=(xx>=2)&(xx<=13)&(zz>=2)&(zz<=13)
        indices[footprint&(((y>=10)&(y<=30)&((xx==2)|(xx==13)|(zz==2)|(zz==13)))|np.isin(y,[10,15,20,25,30]))]=1
        blobs.append(encode(bytes([9,1,sy]),[(indices.ravel(),palette)]))
    with closing(sqlite3.connect(root/'queue.sqlite')) as q:
        q.execute('CREATE TABLE tiles(tx INTEGER,tz INTEGER,state TEXT)')
        for tx in range(tiles):
            q.execute("INSERT INTO tiles VALUES(?,0,'done')",(tx,));chests=[];beds=[]
            for cx in range(tx*side,(tx+1)*side):
                for cz in range(side):
                    db.put(struct.pack('<iiB',cx,cz,43),np.full(256,31,dtype='<i2').tobytes())
                    for sy,blob in enumerate(blobs):db.put(struct.pack('<iiBb',cx,cz,47,sy),blob)
                    identity=hashlib.sha256(f'{cx}/{cz}'.encode()).hexdigest()[:24]
                    plan={'id':identity,'points':[[cx*16+8,cz*16+8],[cx*16+5,cz*16+5],[cx*16+10,cz*16+10]],'bounds':[cx*16+2,cz*16+2,cx*16+13,cz*16+13],'seed':cx*side+cz+1,'placement':'per_floor','probability':1.,'theme':'mixed'}
                    chests.append(plan);beds.append({k:v for k,v in {**plan,'id':hashlib.sha256(('bed/'+identity).encode()).hexdigest()[:24]}.items() if k not in ('placement','probability')})
            data={'bbox':[34.9,135,35.1,136],'treasure':{'version':3,'plans':chests},'beds':{'version':2,'plans':beds}}
            (root/'tile_data'/f'{tx}_0.json').write_text(json.dumps(data),encoding='utf-8')
        q.commit()
    db.close()

def values(root):
    db=leveldb.LevelDB(str(Path(root)/'world/db'),create_if_missing=False)
    try:return dict(db.iterate())
    finally:db.close()

@unittest.skipUnless(NATIVE,'Native runtime required')
class ParallelFurniture(unittest.TestCase):
    def test_parallel_bulk_output_matches_serial_and_retry_keeps_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture(root/'serial');shutil.copytree(root/'serial',root/'parallel');shutil.copytree(root/'serial',root/'rebuild')
            before=time.monotonic();a=ensure_project_furniture(root/'serial',lambda _:None);serial=time.monotonic()-before
            before=time.monotonic();b=ensure_parallel_furniture(root/'parallel',lambda _:None,workers=2,bulk_bytes=65536);parallel=time.monotonic()-before
            self.assertEqual(a['totals'],b['totals']);self.assertGreater(b['totals']['chests'],0)
            self.assertEqual(values(root/'serial'),values(root/'parallel'))
            baseline=values(root/'parallel');ensure_parallel_furniture(root/'parallel',lambda _:None,workers=2)
            self.assertEqual(values(root/'parallel'),baseline)
            c=ensure_parallel_furniture(root/'rebuild',lambda _:None,workers=2,rebuild=True)
            self.assertEqual(c['totals'],a['totals']);self.assertEqual(values(root/'rebuild'),values(root/'serial'))
            self.assertEqual(len(list((root/'rebuild/backups').iterdir())),1)
            gc.collect()

    def test_overlapping_buildings_recalculate_from_prior_tile(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture(root/'serial',tiles=2,side=1)
            first=root/'serial/tile_data/0_0.json';second=root/'serial/tile_data/1_0.json'
            a=json.loads(first.read_text());b=json.loads(second.read_text())
            b['treasure']['plans']=json.loads(json.dumps(a['treasure']['plans']))
            b['treasure']['plans'][0].update(id=hashlib.sha256(b'overlapping-building').hexdigest()[:24],seed=9999)
            for path,data in ((first,a),(second,b)):
                data['beds']['plans']=[];path.write_text(json.dumps(data))
            shutil.copytree(root/'serial',root/'parallel')
            expected=ensure_project_furniture(root/'serial',lambda _:None)
            actual=ensure_parallel_furniture(root/'parallel',lambda _:None,workers=2,rebuild=True)
            self.assertEqual(actual['totals'],expected['totals'])
            self.assertGreater(actual['performance']['conflicts'],0)
            self.assertEqual(values(root/'serial'),values(root/'parallel'))
            gc.collect()

    def test_failed_install_restores_original_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture(root);before=values(root)
            # Exercise a non-canonical spelling even on machines whose temp
            # directory does not use the RUNNER~1 short name seen in Actions.
            (root/'staging').mkdir();fresh=root/'staging'/'..'/'fresh'
            fresh.mkdir();(fresh/'placeholder').write_bytes(b'new')
            import os
            original=os.replace
            def failing(source,target):
                if Path(source).resolve()==fresh.resolve():raise OSError('Simulated installation failure')
                return original(source,target)
            with patch('parallel_furniture.os.replace',side_effect=failing):
                with self.assertRaises(OSError):swap_database(root,fresh,{'records':1})
            self.assertEqual(values(root),before)
            marker=json.loads((root/'parallel-db-swap.json').read_text())
            original(root/'world/db',marker['backup'])
            recover_swap(root)
            self.assertEqual(values(root),before)
            gc.collect()

    def test_district_results_survive_interruption_before_world_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture(root/'serial');shutil.copytree(root/'serial',root/'resumed')
            original=values(root/'resumed');work=root/'resumed/.parallel-furniture-resume'
            def interrupt(message):
                if message.startswith('家具を地区別DBで計算:'):raise InterruptedError('Simulated interruption')
            with self.assertRaises(InterruptedError):
                ensure_parallel_furniture(root/'resumed',interrupt,workers=2,rebuild=True,work_dir=work)
            self.assertEqual(values(root/'resumed'),original)
            hint=json.loads((work/'session.json').read_text())
            messages=[]
            actual=ensure_parallel_furniture(root/'resumed',messages.append,workers=2,rebuild=True,work_dir=work,snapshot_hint=hint)
            expected=ensure_project_furniture(root/'serial',lambda _:None)
            self.assertGreater(actual['performance']['cache_hits'],0)
            self.assertEqual(actual['totals'],expected['totals'])
            self.assertEqual(values(root/'resumed'),values(root/'serial'))
            self.assertTrue(any('データを再利用' in message for message in messages))
            gc.collect()

    def test_reopened_db_reuses_only_shards_whose_source_records_still_match(self):
        from furniture_backend import Blocks
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture(root/'resumed');work=root/'resumed/.parallel-furniture-resume'
            def interrupt(message):
                if message.startswith('家具を地区別DBで計算:'):raise InterruptedError('Interrupted')
            with self.assertRaises(InterruptedError):ensure_parallel_furniture(root/'resumed',interrupt,workers=2,rebuild=True,work_dir=work)
            hint=json.loads((work/'session.json').read_text())
            db=leveldb.LevelDB(str(root/'resumed/world/db'),create_if_missing=False);blocks=Blocks(db)
            blocks.set(8,20,8,'minecraft:air',{});blocks.commit();db.close()
            shutil.copytree(root/'resumed',root/'serial')
            with patch('parallel_furniture.database_fingerprint',return_value='reopened-layout'):
                actual=ensure_parallel_furniture(root/'resumed',lambda _:None,workers=2,rebuild=True,work_dir=work,snapshot_hint=hint,source_aliases=[hint['source_identity']])
            expected=ensure_project_furniture(root/'serial',lambda _:None)
            self.assertGreater(actual['performance']['cache_hits'],0)
            self.assertEqual(actual['totals'],expected['totals']);self.assertEqual(values(root/'resumed'),values(root/'serial'))
            gc.collect()

if __name__=='__main__':unittest.main()
