from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracking import MapReference
from ecology import compile_mobs,options,SPECIES,boss_profile
try:
    import amulet_nbt as nbt
    import leveldb
    import numpy as np
    from geology_backend import encode
    from furniture_backend import Blocks
    from ecology_backend import bake_mobs,surface,mob_nbt,reward_loot
    NATIVE=True
except ImportError:NATIVE=False

class Habitats(unittest.TestCase):
    def setUp(self):self.ref=MapReference('e5147427-4bb2-50c3-9185-53cecc856eaf',(35,139,35.04,139.04),1,4096,4096)
    def way(self,i,tags,bounds):
        a,b,c,d=bounds
        return {'type':'way','id':i,'tags':tags,'geometry':[dict(zip(('lat','lon'),self.ref.geographic(x,z))) for x,z in ((a,b),(c,b),(c,d),(a,d),(a,b))]}
    def test_habitats_density_bounds_and_no_hostiles(self):
        p={'elements':[self.way(1,{'natural':'beach'},(0,0,2048,1024)),self.way(2,{'landuse':'forest'},(0,1024,2048,2048)),self.way(3,{'natural':'bare_rock'},(2048,0,4096,2048)),self.way(4,{'landuse':'farmland'},(0,2048,2048,4096)),self.way(5,{'natural':'water'},(2048,2048,4096,4096))]}
        a=compile_mobs(self.ref,p);b=compile_mobs(self.ref,p,{'density':3})
        self.assertGreater(len(a['groups']),30);self.assertFalse(a['bosses'])
        self.assertGreaterEqual(sum(g['count'] for g in b['groups']),sum(g['count'] for g in a['groups']))
        self.assertEqual(a,compile_mobs(self.ref,{'elements':list(reversed(p['elements']))}))
        for g in a['groups']:
            self.assertIn(g['type'].split(':')[1],dict(SPECIES[g['habitat']]))
            self.assertTrue(all(0<=x<4096 and 0<=z<4096 for x,z in g['points']))
        self.assertFalse(compile_mobs(self.ref,p,{'density':0})['groups'])
        for v in (float('nan'),True,4,-1):
            with self.assertRaises(ValueError):options({'density':v})
    def test_site_area_changes_boss_type_and_small_shrines_work(self):
        p={'elements':[self.way(1,{'religion':'shinto','landuse':'religious'},(50,50,150,150)),self.way(2,{'building':'shrine'},(70,70,130,130)),self.way(3,{'building':'shrine'},(500,500,510,510)),self.way(4,{'landuse':'cemetery'},(2000,2000,2200,2200))]}
        self.assertFalse(compile_mobs(self.ref,p)['bosses'])
        a=compile_mobs(self.ref,p,{'bosses_enabled':True});self.assertEqual(len(a['bosses']),3)
        self.assertTrue(any(b['area_m2']<2000 and b['type']=='minecraft:piglin_brute' for b in a['bosses']))
        self.assertEqual([boss_profile(v)['type'] for v in (100,3000,15000,75000,250000)],['minecraft:piglin_brute','minecraft:elder_guardian','minecraft:warden','minecraft:wither','minecraft:ender_dragon'])
        self.assertEqual(boss_profile(250000)['mode'],'end_portal')
        self.assertLess(boss_profile(100)['health'],boss_profile(1500)['health'])
    def test_national_tile_ownership(self):
        self.ref=replace(self.ref,projection='national_mercator',origin=(35,139),bbox=(34.97,139,35.01,139.04))
        p={'elements':[self.way(1,{'natural':'wood'},(-1024,-1024,2048,2048))]}
        a=compile_mobs(self.ref,p,owned_bounds=(0,0,1024,1024));b=compile_mobs(self.ref,p,owned_bounds=(1024,0,2048,1024))
        self.assertTrue(a['groups'] and b['groups']);self.assertTrue({g['id'] for g in a['groups']}.isdisjoint(g['id'] for g in b['groups']))

@unittest.skipUnless(NATIVE,'Bundled native runtime required')
class NativeMobs(unittest.TestCase):
    def world(self,root):
        db=leveldb.LevelDB(str(root/'db'),create_if_missing=True)
        palette=[nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag('minecraft:'+v),'states':nbt.CompoundTag({}),'version':nbt.IntTag(18168865)})) for v in ('stone','air','grass_block')]
        for cx in range(-1,9):
            for cz in range(-1,9):
                db.put(struct.pack('<iiB',cx,cz,43),np.full(256,64,dtype='<i2').tobytes())
                for sy in range(5):
                    indices=np.zeros(4096,dtype=np.uint32) if sy<4 else np.ones(4096,dtype=np.uint32)
                    if sy==4:indices[::16]=2
                    db.put(struct.pack('<iiBb',cx,cz,47,sy),encode(bytes([9,1,sy]),[(indices,palette)]))
        db.close()
    def test_native_animals_bosses_rewards_pool_portal_and_no_refill(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.world(root)
            bosses=[]
            for index,area in enumerate((100,3000,15000,75000,250000)):
                x=12+index*23
                bosses.append({'id':hashlib.sha256(str(index).encode()).hexdigest()[:24],**boss_profile(area),'name':'Test boss','points':[[x,40],[x,50],[x,58]],'width':2.2})
            compiled={'options':options({'bosses_enabled':True}),'groups':[{'id':'a'*24,'type':'minecraft:goat','points':[[20,80],[24,80]],'count':2,'seed':123}], 'bosses':bosses}
            report=bake_mobs(root,compiled,notify=lambda _:None)
            self.assertEqual(report['counts']['animals'],2);self.assertEqual(report['counts']['bosses'],4)
            self.assertEqual(report['counts']['end_portals'],1);self.assertEqual(report['counts']['reward_chests'],5)
            self.assertFalse(report['runtime_pack']);self.assertFalse((root/'behavior_packs').exists());self.assertFalse((root/'resource_packs').exists())
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);actors=[];chests=[]
            for k,v in db.iterate():
                if k.startswith(b'actorprefix'):actors.append(nbt.load(v,compressed=False,little_endian=True).compound)
                if len(k)==9 and k[8]==49:
                    offset=0
                    while offset<len(v):
                        ctx=nbt.ReadContext();tag=nbt.load(v[offset:],compressed=False,little_endian=True,read_context=ctx);offset+=ctx.offset
                        if str(tag.compound.get('id',''))=='Chest':chests.append(tag.compound)
            self.assertEqual(len(actors),6);self.assertEqual(len(chests),5)
            brute=next(a for a in actors if str(a['identifier'])=='minecraft:piglin_brute')
            self.assertNotIn('+zombification_sensor',[str(v) for v in brute['definitions']]);self.assertIn('+melee_unit',[str(v) for v in brute['definitions']])
            self.assertTrue(all(any(str(i['Name'])=='minecraft:diamond' for i in c['Items']) for c in chests))
            blocks=Blocks(db);self.assertEqual(blocks.get(35,63,40),'minecraft:water');self.assertEqual(blocks.get(104,65,40),'minecraft:end_portal')
            before=dict(db.iterate());db.close()
            self.assertEqual(bake_mobs(root,compiled,notify=lambda _:None),report)
            db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);self.assertEqual(dict(db.iterate()),before);db.close()
    def test_surface_rejects_shallow_water_and_turtle_on_grass(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.world(root);db=leveldb.LevelDB(str(root/'db'),create_if_missing=False);b=Blocks(db)
            self.assertIsNone(surface(b,[20,20],{'type':'minecraft:turtle'}))
            b.set(20,64,20,'minecraft:water',{'liquid_depth':nbt.IntTag(0)});b.commit()
            self.assertIsNone(surface(b,[20,20],{'type':'minecraft:cod','aquatic':True,'min_depth':2}))
            b.set(20,63,20,'minecraft:water',{'liquid_depth':nbt.IntTag(0)});b.commit()
            self.assertIsNotNone(surface(b,[20,20],{'type':'minecraft:cod','aquatic':True,'min_depth':2}));db.close()

if __name__=='__main__':unittest.main()
