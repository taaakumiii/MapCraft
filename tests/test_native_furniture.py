from pathlib import Path
import struct
import sqlite3
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
    def test_independent_floor_draws_and_roof_exclusion(self):
        import hashlib
        from furniture_backend import floor_plans
        class House:
            offset=(0,0)
            def top(self,x,z):return 31
            def block_entities(self,x,z):return b'column',[]
            def get(self,x,y,z):
                return 'minecraft:oak_planks' if 0<=x<=10 and 0<=z<=10 and (y in (10,15,20,25,30) or ((x in (0,10) or z in (0,10)) and 10<=y<=30)) else 'minecraft:air'
        selected=[];partial=False
        for i in range(256):
            plan={'id':hashlib.sha256(str(i).encode()).hexdigest()[:24],'points':[[5,5]],'bounds':[0,0,10,10],'seed':i+1,'placement':'per_floor','probability':.25}
            counts={'incomplete':0,'floors_detected':0,'floors_selected':0};plans=floor_plans(House(),plan,counts)
            self.assertEqual(counts['floors_detected'],4)
            self.assertTrue(all(p['floor_y'] in (11,16,21,26) for p in plans))
            selected.extend(plans);partial|=0<len(plans)<4
            self.assertEqual(plans,floor_plans(House(),plan,{'incomplete':0,'floors_detected':0,'floors_selected':0}))
        self.assertTrue(partial);self.assertTrue(.20<len(selected)/1024<.30)
        for probability,expected in ((0,0),(1,4)):
            self.assertEqual(len(floor_plans(House(),{**plan,'probability':probability},{'incomplete':0,'floors_detected':0,'floors_selected':0})),expected)
        class ThickRoof(House):
            def top(self,x,z):return 41
            def get(self,x,y,z):return 'minecraft:cobbled_deepslate' if 0<=x<=10 and 0<=z<=10 and 30<=y<=40 else super().get(x,y,z)
        self.assertEqual(len(floor_plans(ThickRoof(),{**plan,'probability':1},{'incomplete':0,'floors_detected':0,'floors_selected':0})),4)

    def test_legacy_chests_do_not_bias_individual_floor_odds(self):
        import hashlib
        from furniture_backend import floor_plans
        class House:
            offset=(0,0)
            def __init__(self,identity):self.identity=identity;self.old=int(identity[:8],16)/2**32<.25
            def top(self,x,z):return 31
            def block_entities(self,x,z):
                tag=nbt.NamedTag(nbt.CompoundTag({'id':nbt.StringTag('Chest'),'MapCraftFurnitureId':nbt.StringTag(self.identity),'x':nbt.IntTag(5),'y':nbt.IntTag(21),'z':nbt.IntTag(5)}))
                return b'column',[tag] if self.old else []
            def get(self,x,y,z):
                if self.old and (x,y,z)==(5,21,5):return 'minecraft:chest'
                if y==30 and 0<=x<=10 and 0<=z<=10:return 'minecraft:cobbled_deepslate'
                return 'minecraft:oak_planks' if 0<=x<=10 and 0<=z<=10 and (y in (10,15,20,25) or ((x in (0,10) or z in (0,10)) and 10<=y<=30)) else 'minecraft:air'
        counts={y:0 for y in (11,16,21,26)}
        for i in range(512):
            identity=hashlib.sha256(str(i).encode()).hexdigest()[:24]
            plan={'id':identity,'points':[[5,5]],'bounds':[0,0,10,10],'seed':i+1,'placement':'per_floor','probability':.25}
            for floor in floor_plans(House(identity),plan,{'incomplete':0,'floors_detected':0,'floors_selected':0}):counts[floor['floor_y']]+=1
        self.assertTrue(all(.18<n/512<.32 for n in counts.values()),counts)

    def test_multifloor_migration_and_no_refill(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);db=leveldb.LevelDB(str(root/'db'),create_if_missing=True)
            db.put(struct.pack('<iiB',0,0,43),np.full(256,26,dtype='<i2').tobytes());blocks=Blocks(db)
            for x in range(2,14):
                for z in range(2,14):
                    for y in (10,15,20,25):blocks.set(x,y,z,'minecraft:oak_planks',{})
                    if x in (2,13) or z in (2,13):
                        for y in range(11,25):blocks.set(x,y,z,'minecraft:stone_bricks',{})
            blocks.commit();db.close()
            plan={'id':hashlib.sha256(b'house').hexdigest()[:24],'points':[[7,7],[9,9]],'bounds':[2,2,13,13],'seed':123,'theme':'retail'}
            journal=sqlite3.connect(root/'mapcraft_furniture-state.sqlite')
            journal.execute('CREATE TABLE plans(kind TEXT,id TEXT,status TEXT,data TEXT,PRIMARY KEY(kind,id))')
            journal.execute('INSERT INTO plans VALUES(?,?,?,?)',('chest',plan['id'],'skipped','{}'));journal.commit();journal.close()
            # Simulate an older generator: one top-floor chest, then empty it.
            bake_directory(root,[plan],[],notify=lambda _:None)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);blocks=Blocks(db);key,entities=blocks.block_entities(7,7)
            chest=next(v.compound for v in entities if str(v.compound['id'])=='Chest');self.assertEqual(int(chest['y']),21)
            chest['Items']=nbt.ListTag([]);db.put(key,b''.join(v.save_to(compressed=False,little_endian=True) for v in entities));db.close()
            new={**plan,'placement':'per_floor','probability':1}
            report=bake_directory(root,[new],[],notify=lambda _:None)
            self.assertEqual(report['counts']['chests'],3);self.assertEqual(report['counts']['floors_detected'],3)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);blocks=Blocks(db);key,entities=blocks.block_entities(7,7)
            chests=[v.compound for v in entities if str(v.compound['id'])=='Chest']
            self.assertEqual(sorted(int(v['y']) for v in chests),[11,16,21])
            self.assertFalse(next(v for v in chests if int(v['y'])==21)['Items'])
            blocks.set(7,11,7,'minecraft:air',{});blocks.commit();db.close()
            bake_directory(root,[new],[],notify=lambda _:None)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);blocks=Blocks(db)
            self.assertEqual(blocks.get(7,11,7),'minecraft:air');db.close()

    def test_parallel_geology_matches_serial(self):
        from geology_backend import encode,decorated_records
        palette=[nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag(name),'states':nbt.CompoundTag({}),'version':nbt.IntTag(18168865)}))
                 for name in ('minecraft:stone','minecraft:diamond_ore','minecraft:air')]
        indices=np.zeros(4096,dtype=np.uint32);indices[::11]=1;indices[::13]=2
        data=encode(bytes((9,1,0)),[(indices,palette)])
        rocks=('granite','basalt','volcanic','limestone','sandstone','mudstone','metamorphic','sedimentary','sediment','fill','drained','unknown')
        rows=[(struct.pack('<iiBb',i,-1,47,section),data,i,-1,section,rock,(430000,-75000))
              for i,rock in enumerate(rocks) for section in range(4)]
        self.assertEqual(list(decorated_records(iter(rows),1)),list(decorated_records(iter(rows),4)))

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
