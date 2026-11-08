from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracking import MapReference
try:
    import amulet_nbt as nbt
    import leveldb
    import numpy as np
    from geology_backend import encode
    from furniture_backend import Blocks
    from bathymetry_backend import apply_directory
    NATIVE=True
except ImportError:NATIVE=False

@unittest.skipUnless(NATIVE,'Native runtime required')
class Bathymetry(unittest.TestCase):
    def world(self,root):
        db=leveldb.LevelDB(str(root/'db'),create_if_missing=True)
        names=('stone','air','sand','water','grass_block','cobblestone')
        palette=[nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag('minecraft:'+name),'states':nbt.CompoundTag({'liquid_depth':nbt.IntTag(0)} if name=='water' else {}),'version':nbt.IntTag(18168865)})) for name in names]
        for sy in range(5):
            data=np.zeros((16,16,16),dtype=np.uint32)
            for x in range(16):
                for z in range(16):
                    for yy in range(16):
                        y=sy*16+yy
                        if y>64:data[x,z,yy]=1
                        elif x<12 and y>=61:data[x,z,yy]=3
                        elif x<12 and y==60:data[x,z,yy]=2
                        elif x>=12 and y==64:data[x,z,yy]=4
            if sy==2:data[2,:,45%16]=1
            if sy==3:data[3,:,50%16]=5
            db.put(struct.pack('<iiBb',0,0,47,sy),encode(bytes([9,1,sy]),[(data.ravel(),palette)]))
        db.put(struct.pack('<iiB',0,0,43),np.full(256,64,dtype='<i2').tobytes());db.close()
    def reference(self):return MapReference('e5147427-4bb2-50c3-9185-53cecc856eaf',(35,139,35.001,139.001),1,100,100)
    def survey(self):
        class Survey:
            def elevation(self,lat,lon):return -30.
        return Survey()
    def test_depth_and_seabed_preserve_land_tunnels_structures_and_freshwater(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.world(root)
            report=apply_directory(root,self.reference(),root/'cache',survey=self.survey(),freshwater=lambda x,z:x==4,notify=lambda _:None)
            self.assertGreater(report['counts']['changed_columns'],0);self.assertEqual(report['counts']['protected_columns'],32)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);blocks=Blocks(db)
            self.assertEqual(blocks.get(0,34,0),'minecraft:sand');self.assertEqual(blocks.get(0,35,0),'minecraft:water')
            self.assertEqual(blocks.get(0,33,0),'minecraft:gravel');self.assertEqual(blocks.get(0,32,0),'minecraft:stone')
            self.assertEqual(blocks.get(2,45,0),'minecraft:air');self.assertEqual(blocks.get(2,60,0),'minecraft:sand')
            self.assertEqual(blocks.get(3,50,0),'minecraft:cobblestone');self.assertEqual(blocks.get(4,60,0),'minecraft:sand')
            self.assertEqual(blocks.get(15,64,0),'minecraft:grass_block')
            before=dict(db.iterate());db.close()
            self.assertEqual(apply_directory(root,self.reference(),root/'cache',survey=self.survey(),notify=lambda _:None),report)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);self.assertEqual(dict(db.iterate()),before);db.close()
    def test_retry_after_native_write_is_idempotent_and_deep_water_is_clipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.world(root)
            class Deep:
                def elevation(self,lat,lon):return -5000.
            original=leveldb.LevelDB
            class Interrupt:
                def __init__(self,*args,**kwargs):self.db=original(*args,**kwargs)
                def __getattr__(self,name):return getattr(self.db,name)
                def putBatch(self,batch):self.db.putBatch(batch);raise InterruptedError('After native write')
            with patch('bathymetry_backend.leveldb.LevelDB',Interrupt):
                with self.assertRaises(InterruptedError):apply_directory(root,self.reference(),root/'cache',survey=Deep(),notify=lambda _:None)
            db=original(str(root/'db'),create_if_missing=False);before=dict(db.iterate());db.close()
            report=apply_directory(root,self.reference(),root/'cache',survey=Deep(),notify=lambda _:None)
            self.assertGreater(report['counts']['clipped_columns'],0)
            db=original(str(root/'db'),create_if_missing=False);self.assertEqual(dict(db.iterate()),before)
            self.assertEqual(Blocks(db).get(0,4,0),'minecraft:sand');db.close()

    def test_extension_corrects_new_chunks_without_changing_the_old_seabed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.world(root)
            apply_directory(root,self.reference(),root/'cache',survey=self.survey(),coverage='first',notify=lambda _:None)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);old=dict(db.iterate())
            # A newly generated adjoining chunk starts with the original depth.
            with tempfile.TemporaryDirectory() as source_folder:
                source=Path(source_folder);self.world(source);other=leveldb.LevelDB(str(source/'db'),create_if_missing=False)
                rows={struct.pack('<ii',1,0)+k[8:]:v for k,v in other.iterate()};other.close()
            db.putBatch(rows);db.close()
            report=apply_directory(root,self.reference(),root/'cache',survey=self.survey(),coverage='extended',notify=lambda _:None)
            self.assertEqual(report['coverage'],'extended');self.assertGreater(report['counts']['changed_columns'],0)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False)
            self.assertTrue(all(db.get(k)==v for k,v in old.items()))
            self.assertEqual(Blocks(db).get(16,34,0),'minecraft:sand');db.close()

if __name__=='__main__':unittest.main()
