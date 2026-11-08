from dataclasses import replace
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracking import MapReference
from villagers import compile_villagers,match_population,options
from volcanoes import nearby,grid
from loot_native import loot_for,VOLCANIC_MINERALS

try:
    import amulet_nbt as nbt
    import leveldb
    import numpy as np
    from furniture_backend import Blocks
    from geology_backend import decorate_batch,material_batch,decode,encode
    from villagers_backend import bake_villagers,storage_key
    NATIVE=True
except ImportError:NATIVE=False

class EcologyPlanning(unittest.TestCase):
    def fixture(self):
        ref=MapReference('e5147427-4bb2-50c3-9185-53cecc856eaf',(35,139,35.02,139.02),1,2000,2000)
        towns=[];elements=[]
        for i,name in enumerate(('都会市','田舎町')):
            w=139+i*.01;e=w+.01
            towns.append({'name':name,'bounds':[w,35,e,35.02],'outer':[[[w,35],[e,35],[e,35.02],[w,35.02],[w,35]]],'inner':[]})
            for j in range(20):
                x=w+.001+(j%4)*.002;y=35.001+(j//4)*.003
                ring=[{'lon':a,'lat':b} for a,b in [(x,y),(x+.0004,y),(x+.0004,y+.0004),(x,y+.0004),(x,y)]]
                elements.append({'type':'way','id':i*100+j,'tags':{'building':'house'},'geometry':ring})
        return ref,{'elements':elements},towns

    def test_real_sources_and_disambiguation(self):
        self.assertEqual(sum(len(v) for v in grid().values()),111)
        self.assertIsNone(nearby(35.681236,139.7658))
        self.assertEqual(nearby(35.3606,138.7274)['name'],'富士山')
        self.assertIsNone(match_population({'name':'府中市'}))
        row=match_population({'name':'府中市','local_authority_code':'132063'})
        self.assertEqual(row[1],'東京都');self.assertGreater(row[3],200000)
        self.assertEqual(match_population({'name':'飯島町'})[3],8767)
        self.assertEqual(match_population({'name':'飛驒市'})[3],21295)
        for value in (0,float('nan'),True,1e9):
            with self.assertRaises(ValueError):options({'people_per_villager':value})

    def test_population_area_ratio_controls_counts(self):
        ref,payload,towns=self.fixture()
        data={'as_of':'2026-01-01','municipalities':[['000001','試験県','都会市',24000],['000002','試験県','田舎町',2000]]}
        with patch('villagers.population',return_value=data):
            self.assertFalse(compile_villagers(ref,payload,towns)['plans'])
            settings={'enabled':True,'people_per_villager':1000}
            result=compile_villagers(ref,payload,towns,settings)
            self.assertEqual([a['selected'] for a in result['areas']],[24,2])
            half=compile_villagers(ref,payload,towns,settings,owned_bounds=(0,0,500,2000))
            self.assertEqual([a['selected'] for a in half['areas']],[12])
            fewer=compile_villagers(ref,payload,towns,{**settings,'people_per_villager':2000})
            self.assertEqual([a['selected'] for a in fewer['areas']],[12,1])
            other=compile_villagers(replace(ref,treasure_enabled=False,beds_enabled=False),{'elements':list(reversed(payload['elements']))},towns,settings)
            self.assertEqual(result,other)

    def test_volcanic_loot_keeps_rare_gate_and_food(self):
        normal=mineral=food=0
        for seed in range(2000):
            a=loot_for({'seed':seed,'theme':'retail'})
            b=loot_for({'seed':seed,'theme':'retail','volcano':{'strength':1000}})
            special=lambda items:[(i['type'],i['amount']) for i in items if i['tier'] in ('rare','relic')]
            self.assertEqual(special(a),special(b))
            normal+=sum(i['type'].split(':')[1] in VOLCANIC_MINERALS for i in a)
            mineral+=sum(i['type'].split(':')[1] in VOLCANIC_MINERALS for i in b)
            food+=sum(i['type']=='minecraft:bread' for i in b)
            self.assertTrue(all(i['amount']<=3 for i in b if i['type'].split(':')[1] in VOLCANIC_MINERALS))
        self.assertGreater(mineral,normal*2);self.assertGreater(food,0)

@unittest.skipUnless(NATIVE,'Requires bundled native runtime')
class NativeEcology(unittest.TestCase):
    def test_residents_use_lower_floors_and_optional_safe_yards(self):
        from villagers_backend import resident_position
        class House:
            offset=(0,0)
            def top(self,x,z):return 31
            def get(self,x,y,z):
                if 0<=x<=10 and 0<=z<=10:
                    return 'minecraft:oak_planks' if y in (10,15,20,25,30) or ((x in (0,10) or z in (0,10)) and 10<=y<=30) else 'minecraft:air'
                return 'minecraft:grass_block' if y==10 else 'minecraft:dirt' if y<10 else 'minecraft:air'
        plan={'id':'b'*24,'points':[[5,5]],'bounds':[0,0,10,10],'seed':3}
        self.assertEqual(resident_position(House(),plan,False,set()),[5,11,5])
        exterior=resident_position(House(),plan,True,set())
        self.assertEqual(exterior[1],11);self.assertFalse(0<=exterior[0]<=10 and 0<=exterior[2]<=10)
    def test_sealed_magma_preserves_tunnel_and_cpu_gpu_codec_parity(self):
        from geology_backend import material_descriptor
        from gpu_driver import MaterialsGPU
        palette=[nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag('minecraft:'+name),'states':nbt.CompoundTag({}),'version':nbt.IntTag(18168865)})) for name in ('stone','air','iron_ore')]
        indices=np.zeros((16,16,16),dtype=np.uint32);indices[:,8,8]=1;indices[8,7,8]=2
        original=encode(bytes([9,1,1]),[(indices.ravel(),palette)])
        volcano={'strength':1000,'x':8,'z':8,'radius':100,'y':24,'ry':5}
        row=(b'chunk',original,0,0,1,'volcanic',(0,0),volcano)
        cpu=decorate_batch([row])[0];result=decode(cpu[1])[0]
        names=np.array([str(p.compound['name']) for p in result[1]])[result[0]].reshape(16,16,16)
        self.assertTrue((names[:,8,8]=='minecraft:air').all());self.assertEqual(names[8,7,8],'minecraft:iron_ore')
        self.assertTrue((names=='minecraft:lava').any())
        for x,z,y in zip(*np.where(names=='minecraft:lava')):
            self.assertTrue(2<=x<=13 and 2<=z<=13 and 2<=y<=13)
            self.assertTrue(all(indices[x+dx,z+dz,y+dy]==0 for dx,dz,dy in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1))))
        import os
        if os.environ.get('MAPCRAFT_TEST_GPU')=='1':
            gpu=MaterialsGPU()
            try:self.assertEqual(cpu,material_batch(([row],gpu.materials([material_descriptor(row)])))[0])
            finally:gpu.close()

    def test_native_resident_digest_retry_and_no_respawn(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=leveldb.LevelDB(str(root/'db'),create_if_missing=True)
            db.put(struct.pack('<iiB',0,0,43),np.full(256,17,dtype='<i2').tobytes());blocks=Blocks(db)
            for x in range(2,14):
                for z in range(2,14):
                    for y in range(10,17):
                        if y in (10,16) or x in (2,13) or z in (2,13):blocks.set(x,y,z,'minecraft:oak_planks',{})
            unrelated=nbt.NamedTag(nbt.CompoundTag({'identifier':nbt.StringTag('minecraft:pig')})).save_to(compressed=False,little_endian=True)
            blocks.commit();db.put(b'actorprefix'+b'OTHERKEY',unrelated);db.close()
            plan={'id':'resident1','seed':1,'points':[[5,5]],'bounds':[2,2,13,13]}
            compiled={'plans':[plan],'options':{'enabled':True,'people_per_villager':1000}}
            report=bake_villagers(root,compiled,notify=lambda _:None)
            self.assertEqual(report['counts']['placed'],1)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False)
            key=db.get(b'digp'+struct.pack('<ii',0,0));self.assertEqual(len(key),8)
            tag=nbt.load(db.get(b'actorprefix'+key),compressed=False,little_endian=True).compound
            self.assertEqual(storage_key(int(tag['UniqueID'])),key)
            self.assertEqual(str(tag['identifier']),'minecraft:villager_v2')
            self.assertEqual([float(v) for v in tag['Pos']],[5.5,11,5.5])
            self.assertEqual(db.get(b'actorprefix'+b'OTHERKEY'),unrelated);db.close()
            self.assertEqual(bake_villagers(root,compiled,notify=lambda _:None)['counts']['placed'],0)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);db.delete(b'actorprefix'+key);db.put(b'digp'+struct.pack('<ii',0,0),b'');db.close()
            self.assertEqual(bake_villagers(root,compiled,notify=lambda _:None)['counts']['placed'],0)

if __name__=='__main__':unittest.main()
