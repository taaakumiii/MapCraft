from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loot_native import loot_for
try:
    import leveldb
    import numpy as np
    import amulet_nbt as nbt
    from furniture_backend import Blocks,bake_directory
    NATIVE=True
except ImportError:NATIVE=False


class LootBalance(unittest.TestCase):
    def test_deterministic_balanced_loot(self):
        rare=0
        for theme in ('retail','urban','farm','industrial','coast','forest','mixed'):
            for seed in range(200):
                plan={'seed':seed,'theme':theme}
                items=loot_for(plan)
                self.assertEqual(items,loot_for(plan))
                self.assertTrue(3<=len(items)<=6)
                valuable=[i for i in items if i['tier'] in ('rare','relic')]
                self.assertLessEqual(len(valuable),1)
                self.assertTrue(all(i['amount']==1 for i in valuable))
                self.assertEqual(len({i['slot'] for i in items}),len(items))
                rare+=bool(valuable)
        self.assertLess(rare,1400*.03)


@unittest.skipUnless(NATIVE,'Run with the bundled native Python runtime')
class NativeFurniture(unittest.TestCase):
    def test_cross_chunk_bed_inventory_and_no_refill(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=True)
            for cx in (0,1):db.put(struct.pack('<iiB',cx,0,43),np.full(256,16,dtype='<i2').tobytes())
            blocks=Blocks(db)
            for x in range(10,26):
                for z in range(13):
                    for y in (10,15):blocks.set(x,y,z,'minecraft:oak_planks',{})
                    if x in (10,25) or z in (0,12):
                        for y in range(11,15):blocks.set(x,y,z,'minecraft:stone_bricks',{})
            blocks.commit();db.close()
            chest={'id':'chest','seed':123,'theme':'retail','points':[[15,5]],'bounds':[10,0,25,12]}
            bed={'id':'bed','seed':3,'points':[[15,9]],'bounds':[10,0,25,12]}
            result=bake_directory(root,[chest],[bed],notify=lambda _:None)
            self.assertEqual((result['counts']['chests'],result['counts']['beds']),(1,1))
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);blocks=Blocks(db)
            self.assertEqual(blocks.get(15,11,5),'minecraft:chest')
            key,entities=blocks.block_entities(15,5)
            entity=next(e.compound for e in entities if str(e.compound['id'])=='Chest')
            self.assertGreaterEqual(len(entity['Items']),3)
            for x,head in ((15,0),(16,1)):
                self.assertEqual(blocks.get(x,11,9),'minecraft:bed')
                _,(_,layers),i=blocks.section(x,11,9)
                states=layers[0][1][int(layers[0][0][i])].compound['states']
                self.assertEqual(int(states['head_piece_bit']),head)
            entity['Items']=nbt.ListTag([])
            db.put(key,b''.join(e.save_to(compressed=False,little_endian=True) for e in entities))
            blocks.set(15,11,9,'minecraft:air',{});blocks.commit();db.close()
            result=bake_directory(root,[chest],[bed],notify=lambda _:None)
            self.assertEqual(result['counts']['existing'],2)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);blocks=Blocks(db)
            self.assertEqual(blocks.get(15,11,9),'minecraft:air')
            _,entities=blocks.block_entities(15,5)
            self.assertEqual(len(next(e.compound for e in entities if str(e.compound['id'])=='Chest')['Items']),0)
            db.close()
            # A tile's discarded padding must never receive half of a bed.
            edge={'id':'edge-bed','seed':3,'points':[[15,2]],'bounds':[10,0,25,12]}
            result=bake_directory(root,[],[edge],owned_bounds=(0,0,16,16),notify=lambda _:None)
            self.assertEqual(result['counts']['beds'],1)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);blocks=Blocks(db)
            self.assertEqual(blocks.get(15,11,2),'minecraft:bed')
            self.assertEqual(blocks.get(15,11,3),'minecraft:bed')
            self.assertEqual(blocks.get(16,11,2),'minecraft:air')
            db.close()


if __name__=='__main__':unittest.main()
