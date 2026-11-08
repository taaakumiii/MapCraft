import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import amulet_nbt as nbt
    from ocean_backend import apply_directory,flat_layers,options
    NATIVE=True
except ImportError:NATIVE=False


@unittest.skipUnless(NATIVE,'Native runtime required')
class Ocean(unittest.TestCase):
    def world(self,root,generator=2):
        root.mkdir(exist_ok=True)
        tag=nbt.NamedTag(nbt.CompoundTag({'Generator':nbt.IntTag(generator),
            'FlatWorldLayers':nbt.StringTag('{"biome_id":1,"preset_id":"TheVoid"}'),
            'SpawnX':nbt.IntTag(434095),'SpawnY':nbt.IntTag(66),'SpawnZ':nbt.IntTag(-76069),
            'LevelName':nbt.StringTag('Tokyo'),'RandomSeed':nbt.LongTag(123456789)}))
        body=tag.save_to(compressed=False,little_endian=True)
        (root/'level.dat').write_bytes(struct.pack('<II',10,len(body))+body)
        (root/'db').mkdir();(root/'db/untouched').write_bytes(b'Existing terrain, actors and inventory')
        return tag
    def test_native_ocean_layers_match_the_existing_water_level(self):
        layers=flat_layers(64,60);y=-64;spans={}
        for layer in layers['block_layers']:
            count=layer['count'];self.assertGreater(count,0)
            spans[layer['block_name']]=(y,y+count-1);y+=count
        self.assertEqual(spans['minecraft:water'],(5,64));self.assertEqual(spans['minecraft:sand'],(4,4))
        self.assertEqual(spans['minecraft:gravel'],(3,3));self.assertEqual(spans['minecraft:stone'],(-63,2))
        self.assertEqual(y,65);self.assertEqual(layers['biome_id'],0)
    def test_preserves_existing_chunks_identity_spawn_and_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'world';tag=self.world(root);before=(root/'level.dat').read_bytes()
            report=apply_directory(root,{'enabled':True,'depth':60},notify=lambda _:None)
            actual=nbt.load((root/'level.dat').read_bytes()[8:],compressed=False,little_endian=True).compound
            for key in tag.compound:
                if key!='FlatWorldLayers':self.assertEqual(tag.compound[key],actual[key])
            self.assertEqual((root/'db/untouched').read_bytes(),b'Existing terrain, actors and inventory')
            self.assertEqual(next((Path(folder)/'backups').glob('*.dat')).read_bytes(),before)
            after=(root/'level.dat').read_bytes();self.assertFalse(report['runtime_pack'])
            self.assertEqual(apply_directory(root,{'enabled':True,'depth':60},notify=lambda _:None),report)
            self.assertEqual((root/'level.dat').read_bytes(),after)
    def test_disabled_and_unexpected_world_do_not_change_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'world';self.world(root,generator=1);before=(root/'level.dat').read_bytes()
            self.assertIsNone(apply_directory(root))
            with self.assertRaises(ValueError):apply_directory(root,{'enabled':True})
            self.assertEqual((root/'level.dat').read_bytes(),before)
            for settings in ({'enabled':'true'},{'depth':0},{'depth':True}):
                with self.assertRaises(ValueError):options(settings)
            with self.assertRaises(ValueError):flat_layers(4,120)


if __name__=='__main__':unittest.main()
