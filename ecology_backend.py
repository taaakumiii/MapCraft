"""Creator-only vanilla mobs, bosses and reward chests. No runtime pack needed."""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import amulet_nbt as nbt
import leveldb
from furniture_backend import Blocks,MissingChunk
from villagers_backend import actor as base_actor,storage_key
from loot_native import item_nbt,random

GROUPS={
 'turtle':['minecraft:adult'],'rabbit':['adult','coat_brown'],
 'goat':['goat_adult','goat_default','interact_default','ram_default'],
 'cow':['minecraft:cow_adult'],'sheep':['minecraft:sheep_adult','minecraft:sheep_dyeable','minecraft:rideable_wooly','minecraft:loot_wooly','minecraft:sheep_white'],
 'pig':['minecraft:pig_adult','minecraft:pig_unsaddled'],'chicken':['minecraft:chicken_adult'],
 'horse':['minecraft:horse_adult','minecraft:horse_wild','minecraft:base_brown','minecraft:markings_none'],
 'donkey':['minecraft:donkey_adult','minecraft:donkey_wild'],
 'llama':['minecraft:llama_adult','minecraft:llama_wild','minecraft:llama_unleashed','minecraft:llama_creamy','minecraft:strength_1'],
 'wolf':['minecraft:wolf_adult','minecraft:wolf_wild','minecraft:wolf_leashable','minecraft:wolf_pale'],
 'fox':['minecraft:fox_adult','minecraft:docile_fox','minecraft:fox_red','minecraft:fox_day','minecraft:fox_ambient_normal'],
 'bee':['bee_adult','track_attacker','shelter_detection','look_for_food'],
 'cat':['minecraft:cat_adult','minecraft:cat_wild','minecraft:cat_tabby'],'frog':['temperate_frog'],
 'cod':[],'salmon':['scale_normal'],'squid':['minecraft:squid_adult'],'dolphin':['dolphin_adult','dolphin_swimming_navigation'],
 'piglin_brute':['alert_for_attack_targets','melee_unit'],
 'elder_guardian':[],'warden':[],'wither':[]}
HEALTH={'turtle':30,'rabbit':3,'goat':10,'cow':10,'sheep':8,'pig':10,'chicken':4,'horse':22,'donkey':22,'llama':22,'wolf':8,'fox':10,'bee':10,'cat':10,'frog':10,'cod':3,'salmon':3,'squid':10,'dolphin':10}
NATURAL={'grass','grass_block','dirt','coarse_dirt','rooted_dirt','podzol','mycelium','stone','sand','red_sand','gravel','snow','snow_layer','clay','mud','moss_block','andesite','granite','diorite','tuff','calcite','deepslate','terracotta'}
AIR={'air','short_grass','tallgrass','tall_grass','fern','large_fern','yellow_flower','red_flower','dandelion','poppy','deadbush','pink_petals','wildflowers'}
WATER={'water','flowing_water'}

def name(blocks,x,y,z):return blocks.get(x,y,z).removeprefix('minecraft:')

def surface(blocks,point,plan):
    x,z=point
    try:
        top=blocks.top(x,z)
        for y in range(top,max(-63,top-48),-1):
            floor=name(blocks,x,y,z)
            if plan.get('aquatic'):
                if floor not in WATER:continue
                radius=1 if plan.get('width',.9)>1 else 0
                if all(name(blocks,x+dx,y-d,z+dz) in WATER for dx in range(-radius,radius+1) for dz in range(-radius,radius+1) for d in range(plan.get('min_depth',2))):return [x,y-1,z]
                return None
            if floor not in NATURAL:continue
            radius=1 if plan.get('width',.9)>1 else 0
            height=8 if plan['type']=='minecraft:wither' else 3
            if not all(name(blocks,x+dx,y,z+dz) in NATURAL and all(name(blocks,x+dx,y+h,z+dz) in AIR for h in range(1,height+1)) for dx in range(-radius,radius+1) for dz in range(-radius,radius+1)):return None
            if plan['type']=='minecraft:turtle':
                if floor!='sand':return None
                if not any(name(blocks,x+dx,y+dy,z+dz) in WATER for step in (4,8,12,16,20,24) for dx,dz in ((step,0),(-step,0),(0,step),(0,-step)) for dy in (-1,0)):return None
            return [x,y+1,z]
    except MissingChunk:return None
    return None

def mob_nbt(uid,position,plan):
    tag=base_actor(uid,position,{**plan,'seed':plan.get('seed',int(plan['id'][:8],16))});body=tag.compound
    short=plan['type'].split(':')[1];health=plan.get('health',HEALTH.get(short,20))
    body['identifier']=nbt.StringTag(plan['type'])
    if short=='piglin_brute':body['Variant']=nbt.IntTag(1)
    definitions=['+'+plan['type']]+['+'+g for g in GROUPS.get(short,[])]
    # Brutes are deliberately saved without the vanilla overworld conversion
    # sensor. This is saved vanilla component-group state, not a custom entity.
    if short=='piglin_brute':definitions+=['-zombification_sensor','-start_zombification','-become_zombie']
    body['definitions']=nbt.ListTag([nbt.StringTag(v) for v in definitions])
    body['Health']=nbt.ShortTag(health);body['Attributes']=nbt.ListTag([nbt.CompoundTag({
        'Name':nbt.StringTag('minecraft:health'),'Base':nbt.FloatTag(health),'Current':nbt.FloatTag(health),'Max':nbt.FloatTag(health),'Min':nbt.FloatTag(0)})])
    body['MapCraftMobId']=nbt.StringTag(plan['id']);del body['MapCraftVillagerId']
    if plan.get('boss'):
        body['CustomName']=nbt.StringTag(plan['name']);body['CustomNameVisible']=nbt.ByteTag(1)
    if short=='piglin_brute':
        body['Mainhand']=nbt.ListTag([nbt.CompoundTag({'Name':nbt.StringTag('minecraft:golden_axe'),'Count':nbt.ByteTag(1),'Damage':nbt.ShortTag(0),'WasPickedUp':nbt.ByteTag(0)})])
    return tag

def reward_loot(plan):
    r=random(int(plan['id'][:8],16));tier=plan['tier']
    stacks=[('cooked_beef',8+int(r()*9)),('golden_carrot',3+int(r()*5)),('emerald',3+int(r()*min(14,3+tier*2))),
            ('diamond',1+int(r()*(tier+1))),('enchanted_book',1)]
    if tier>=3:stacks.append(('golden_apple',1+int(r()*2)))
    if tier>=4:stacks.append((('netherite_ingot' if r()<.35 else 'netherite_upgrade_smithing_template'),1))
    if tier==5 and r()<.1:stacks.append(('elytra',1))
    result=[]
    for slot,(kind,amount) in enumerate(stacks):
        item=item_nbt({'type':'minecraft:'+kind,'amount':amount,'seed':int(r()*2**32),'slot':slot})
        if kind=='enchanted_book':
            # Bedrock vanilla enchantment IDs: Unbreaking / Efficiency / Protection / Mending.
            enchant,level=((17,min(3,tier+1)),(15,min(5,tier+1)),(0,min(4,tier+1)),(26,1))[int(r()*4)]
            item['tag']=nbt.CompoundTag({'ench':nbt.ListTag([nbt.CompoundTag({'id':nbt.ShortTag(enchant),'lvl':nbt.ShortTag(level)})])})
        result.append(item)
    return nbt.ListTag(result)

def arena(blocks,position,mode,*,apply=False,restore=False):
    x,y,z=position;ground=y-1;radius=3 if mode=='pool' else 2;depth=3 if mode=='pool' else 1
    if not restore:
        for dx in range(-radius,radius+1):
            for dz in range(-radius,radius+1):
                for dy in range(-depth,1):
                    if name(blocks,x+dx,ground+dy,z+dz) not in NATURAL:return False
                if any(name(blocks,x+dx,y+h,z+dz) not in AIR for h in range(4)):return False
    if not apply:return True
    for dx in range(-radius,radius+1):
        for dz in range(-radius,radius+1):
            if mode=='pool':
                for dy in range(-3,1):
                    water=abs(dx)<radius and abs(dz)<radius and dy>-3
                    blocks.set(x+dx,ground+dy,z+dz,'minecraft:water' if water else 'minecraft:stone',{'liquid_depth':nbt.IntTag(0)} if water else {})
            else:
                blocks.set(x+dx,ground,z+dz,'minecraft:obsidian',{})
                if abs(dx)<2 and abs(dz)<2:
                    blocks.set(x+dx,y,z+dz,'minecraft:end_portal',{})
                    if not blocks.has_entity(x+dx,y,z+dz):blocks.entity(x+dx,y,z+dz,'EndPortal','mapcraft/end-portal',{})
    return True

def bake_mobs(world,compiled,*,state_path=None,notify=print):
    world=Path(world);marker=world/'mapcraft_ecology.json'
    signature=hashlib.sha256(json.dumps(compiled,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if marker.exists():
        old=json.loads(marker.read_text(encoding='utf-8'))
        if old.get('signature')==signature:return old
    journal=sqlite3.connect(state_path or world/'mapcraft_ecology-state.sqlite')
    journal.execute('CREATE TABLE IF NOT EXISTS actors(id TEXT PRIMARY KEY,state TEXT,data TEXT)');journal.commit()
    db=None;counts={'animals':0,'bosses':0,'end_portals':0,'reward_chests':0,'existing':0,'blocked':0}
    try:
        db=leveldb.LevelDB(str(world/'db'),create_if_missing=False);blocks=Blocks(db,(0,0));used=set()
        if not journal.execute('SELECT 1 FROM actors LIMIT 1').fetchone():
            for key,data in db.iterate():
                if not key.startswith(b'actorprefix'):continue
                tag=nbt.load(data,compressed=False,little_endian=True).compound;identity=str(tag.get('MapCraftMobId',''))
                if identity:journal.execute('INSERT OR IGNORE INTO actors VALUES(?,?,?)',(identity,'placed','{}'))
            journal.commit()
        sequence=int.from_bytes(blocks.value(b'mapcraft_villager_sequence') or b'\0','little')
        plans=[]
        for group in compiled['groups']:
            for i in range(group['count']):
                offset=i%len(group['points']);plans.append({**group,'id':hashlib.sha256((group['id']+'/'+str(i)).encode()).hexdigest()[:24],'points':group['points'][offset:]+group['points'][:offset]})
        plans += [{**p,'boss':True} for p in compiled['bosses']]
        for number,plan in enumerate(plans):
            previous=journal.execute('SELECT state,data FROM actors WHERE id=?',(plan['id'],)).fetchone()
            if previous and previous[0]=='placed':counts['existing']+=1;continue
            saved=json.loads(previous[1]) if previous else {}
            position=saved.get('position')
            if position is None:
                for point in plan['points']:
                    candidate=surface(blocks,point,plan)
                    if candidate and all((candidate[0]+dx,candidate[2]+dz) not in used for dx in (-1,0,1) for dz in (-1,0,1)):
                        if plan.get('mode') in ('pool','end_portal'):
                            try:
                                if not arena(blocks,candidate,plan['mode']):continue
                            except MissingChunk:continue
                        position=candidate;break
            if position is None:counts['blocked']+=1;continue
            reward=saved.get('reward')
            if plan.get('boss') and reward is None:
                for point in plan['points']:
                    p=surface(blocks,point,{'type':'minecraft:chest','width':.9})
                    if p and math.hypot(p[0]-position[0],p[2]-position[2])>=5 and not blocks.has_entity(*p):reward=p;break
                if reward is None:counts['blocked']+=1;continue
            key=bytes.fromhex(saved['key']) if saved.get('key') else None
            portal=plan.get('mode')=='end_portal'
            if key is None and not portal:
                while True:
                    sequence+=1
                    if sequence>=2**32:raise ValueError('モブの保存IDが不足しました。')
                    key=storage_key(-2**32+sequence)
                    if blocks.value(b'actorprefix'+key) is None:break
            saved={'position':position,'key':key.hex() if key else None,'reward':reward}
            journal.execute('INSERT OR REPLACE INTO actors VALUES(?,?,?)',(plan['id'],'placing',json.dumps(saved)));journal.commit()
            if plan.get('mode') in ('pool','end_portal'):
                arena(blocks,position,plan['mode'],apply=True,restore=bool(previous))
                blocks.commit()
            x,y,z=position
            if portal:counts['end_portals']+=1
            elif blocks.value(b'actorprefix'+key) is None:
                uid=((int.from_bytes(key,'big')-0x100000000)^0xffffffff00000000)&0xffffffffffffffff
                if uid>=2**63:uid-=2**64
                py=y-2 if plan.get('mode')=='pool' else y+3 if plan['type']=='minecraft:wither' else y
                tag=mob_nbt(uid,[x+.5,py,z+.5],plan)
                digest_key=b'digp'+struct.pack('<ii',x//16,z//16);digest=blocks.value(digest_key) or b''
                if len(digest)%8:raise ValueError('Actor参照が壊れています。モブ配置を中止します。')
                version_key=struct.pack('<iiB',x//16,z//16,44);version=blocks.value(version_key)
                batch={b'actorprefix'+key:tag.save_to(compressed=False,little_endian=True),digest_key:digest+key,b'mapcraft_villager_sequence':sequence.to_bytes(4,'little')}
                if version is None or version[0]<40:batch[version_key]=bytes([40])
                db.putBatch(batch);counts['bosses' if plan.get('boss') else 'animals']+=1
            used.add((x,z))
            if reward and not blocks.has_entity(*reward):
                cx,cy,cz=reward
                blocks.set(cx,cy,cz,'minecraft:chest',{'minecraft:cardinal_direction':nbt.StringTag('north')})
                blocks.entity(cx,cy,cz,'Chest',plan['id']+'/reward',{'Findable':nbt.ByteTag(0),'Items':reward_loot(plan)})
                counts['reward_chests']+=1
            blocks.commit();journal.execute("UPDATE actors SET state='placed' WHERE id=?",(plan['id'],));journal.commit()
            if number%100==0:notify(f'モブを事前配置: 動物{counts["animals"]:,} / ボス{counts["bosses"]:,} / 攻略宝箱{counts["reward_chests"]:,}')
        report={'version':1,'mode':'native_vanilla_mobs','signature':signature,'options':compiled['options'],'counts':counts,
                'runtime_pack':False,'dragon':'Large sites use a vanilla End portal; the dragon is fought in the normal End.'}
        marker.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        (world/'ECOLOGY-CREDITS.txt').write_text('© OpenStreetMap contributors / ODbL-1.0\nhttps://www.openstreetmap.org/copyright\n生息地・敷地面積はOSMに基づくゲーム用の近似です。Minecraft標準のモブ・ブロック・アイテムを生成時に保存します。\n',encoding='utf-8')
        return report
    finally:
        if db is not None:db.close()
        journal.close()

def ensure_project_ecology(root,notify=print):
    import national_backend as n
    from tracking import MapReference
    from ecology import options,compile_mobs
    root=Path(root);config=n.load_project(root);settings=options(config.get('mobs',{'wildlife_enabled':False,'bosses_enabled':False}))
    if not settings['wildlife_enabled'] and not settings['bosses_enabled']:return
    ref=MapReference(config['map_id'],tuple(config['bbox']),config['scale'],1,1,projection=config['projection'],origin=tuple(config['origin']))
    result={'version':1,'map_id':ref.map_id,'options':settings,'groups':[],'bosses':[],'source':'OpenStreetMap'}
    with n.connect(root/'queue.sqlite') as queue,n.connect(config['index']) as source:
        tiles=queue.execute("SELECT tx,tz FROM tiles WHERE state='done' ORDER BY tz,tx").fetchall()
        for i,(tx,tz) in enumerate(tiles):
            path=root/'tile_data'/f'{tx}_{tz}.json';data=json.loads(path.read_text(encoding='utf-8'));compiled=data.get('ecology')
            if compiled is None or compiled.get('options')!=settings:
                size=config['tile'];compiled=compile_mobs(ref,n.extract(source,data['bbox']),settings,owned_bounds=(tx*size,tz*size,(tx+1)*size,(tz+1)*size))
                data['ecology']=compiled;n.atomic_json(path,data)
            result['groups'].extend(compiled['groups']);result['bosses'].extend(compiled['bosses'])
            if i%20==0:notify(f'動物とボスの配置計画: {i+1:,}/{len(tiles):,} 区画')
    for key in ('groups','bosses'):result[key]=list({p['id']:p for p in result[key]}.values())
    return bake_mobs(root/'world',result,state_path=root/'ecology-state.sqlite',notify=notify)
