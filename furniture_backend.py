"""Bake ordinary Bedrock chests, inventories and beds into private creator outputs.

Never point this at an installed/played world. A small creator-side journal
bridges SQLite and LevelDB commits; retries never refill an existing chest.
"""
import argparse
from collections import OrderedDict
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
import tempfile
import zipfile
BASE=Path(__file__).resolve().parent;sys.path.insert(0,str(BASE))
import amulet_nbt as nbt
import leveldb
import numpy as np
from geology_backend import decode,encode
from loot_native import loot_for,item_nbt
from tracking import MapReference
VERSION=2
DIRECTIONS=[(0,1),(-1,0),(0,-1),(1,0)]
NATURAL=re.compile(r'(?:leaves|log|wood|stem|hyphae|grass|dirt|sand|gravel|water|lava|snow|ice|vine|flower|sapling|mushroom|bamboo|cactus|air)$')
STRUCTURE=re.compile(r'(?:planks|bricks?|brick_block|stonebrick|concrete|terracotta|wool|glass|quartz|sandstone|polished_|smooth_|purpur|prismarine|copper|tuff_brick|deepslate_tile|blackstone|cobblestone|cobbled_deepslate)')
EXCLUDE=re.compile(r'(?:chest|barrel|torch|lantern|carpet|rail|ladder|fence|button|pressure_plate|trapdoor|door|pane|bars)$')

class MissingChunk(Exception):pass

def structural(name):
    return name!='minecraft:air' and not NATURAL.search(name) and bool(STRUCTURE.search(name)) and not EXCLUDE.search(name)
def support(name):return structural(name) and not re.search(r'(?:slab|stairs)$',name)
def furniture(name):return bool(re.search(r'(?:bed|chest|barrel|door)$',name))

class Blocks:
    def __init__(self,db,offset=(0,0)):
        self.db=db;self.offset=offset;self.cache=OrderedDict();self.dirty=set();self.entities={};self.entity_dirty=set()
    def local(self,x,y,z):return int(x-self.offset[0]),int(y),int(z-self.offset[1])
    def value(self,key):
        try:return self.db.get(key)
        except KeyError:return None
    def section(self,x,y,z):
        cx,cz,sy=x//16,z//16,y//16;key=struct.pack('<iiBb',cx,cz,47,sy)
        if key not in self.cache:
            data=self.value(key)
            if data is None:
                if self.value(struct.pack('<iiB',cx,cz,43)) is None:raise MissingChunk((cx,cz,sy))
                # An absent section in an existing column is ordinary air.
                air=nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag('minecraft:air'),'states':nbt.CompoundTag({}),'version':nbt.IntTag(18168865)}))
                self.cache[key]=[struct.pack('BBB',9,1,sy&255),[(np.zeros(4096,dtype=np.uint32),[air])]]
            else:self.cache[key]=[data[:3],decode(data)]
        self.cache.move_to_end(key)
        while len(self.cache)>256:
            oldest=next(iter(self.cache))
            if oldest==key:break
            if oldest in self.dirty:
                self.cache.move_to_end(oldest)
                if len(self.dirty)>=len(self.cache):break
            else:self.cache.popitem(last=False)
        return key,self.cache[key],(x%16)*256+(z%16)*16+y%16
    def get(self,x,y,z):
        x,y,z=self.local(x,y,z)
        if y<-64 or y>=512:raise MissingChunk('height')
        _,(_,layers),i=self.section(x,y,z);indices,palette=layers[0]
        return str(palette[int(indices[i])].compound['name'])
    def top(self,x,z):
        x,_,z=self.local(x,0,z);data=self.value(struct.pack('<iiB',x//16,z//16,43))
        if data is None or len(data)<512:raise MissingChunk('heightmap')
        return min(510,struct.unpack_from('<h',data,2*((z%16)*16+x%16))[0]+1)
    def set(self,x,y,z,name,states):
        x,y,z=self.local(x,y,z);key,(_,layers),i=self.section(x,y,z);indices,palette=layers[0]
        compound=nbt.CompoundTag({'name':nbt.StringTag(name),'states':nbt.CompoundTag(states),'version':palette[0].compound['version']})
        slot=next((j for j,tag in enumerate(palette) if tag.compound==compound),None)
        if slot is None:slot=len(palette);palette.append(nbt.NamedTag(compound))
        indices[i]=slot;self.dirty.add(key)
    def block_entities(self,x,z):
        x,_,z=self.local(x,0,z);key=struct.pack('<iiB',x//16,z//16,49)
        if key not in self.entities:
            data=self.value(key) or b'';cursor=0;values=[]
            while cursor<len(data):
                context=nbt.ReadContext();tag=nbt.load(data[cursor:],compressed=False,little_endian=True,read_context=context)
                if not context.offset:raise ValueError('Block entity offset did not advance')
                values.append(tag);cursor+=context.offset
            self.entities[key]=values
        return key,self.entities[key]
    def entity(self,x,y,z,kind,identity,extra):
        key,values=self.block_entities(x,z);lx,ly,lz=self.local(x,y,z)
        if any(all(int(v.compound.get(k,nbt.IntTag(-2**31)))==coord for k,coord in [('x',lx),('y',ly),('z',lz)]) for v in values):
            raise ValueError('Refusing to replace an existing block entity')
        body={'id':nbt.StringTag(kind),'x':nbt.IntTag(lx),'y':nbt.IntTag(ly),'z':nbt.IntTag(lz),
              'isMovable':nbt.ByteTag(1),'MapCraftFurnitureId':nbt.StringTag(identity),**extra}
        values.append(nbt.NamedTag(nbt.CompoundTag(body)));self.entity_dirty.add(key)
    def has_entity(self,x,y,z):
        _,values=self.block_entities(x,z);lx,ly,lz=self.local(x,y,z)
        return any(all(int(v.compound.get(k,nbt.IntTag(-2**31)))==c for k,c in [('x',lx),('y',ly),('z',lz)]) for v in values)
    def commit(self):
        batch={key:encode(self.cache[key][0],self.cache[key][1]) for key in self.dirty}
        for key in self.entity_dirty:batch[key]=b''.join(v.save_to(compressed=False,little_endian=True) for v in self.entities[key])
        if batch:self.db.putBatch(batch)
        self.dirty.clear();self.entity_dirty.clear();self.entities.clear()
        while len(self.cache)>256:self.cache.popitem(last=False)

def legacy_structural(name):return structural(name) and not re.search(r'(?:cobblestone|cobbled_deepslate)',name)

def enclosed(blocks,plan,x,y,z,roof=False,legacy=False):
    is_structure=legacy_structural if legacy else structural
    west,north,east,south=plan['bounds']
    if roof:
        if not west<x<east or not north<z<south:return False
        for height in range(y+2,min(y+18,511)):
            name=blocks.get(x,height,z)
            if name!='minecraft:air':
                if not is_structure(name):return False
                break
        else:return False
    for dx,dz in DIRECTIONS:
        limit=min(64,east-x+2 if dx>0 else x-west+2 if dx<0 else south-z+2 if dz>0 else z-north+2)
        if not any(is_structure(name:=blocks.get(x+dx*step,y+1,z+dz*step)) or re.search(r'(?:door|glass_pane|iron_bars)$',name) for step in range(1,max(1,limit)+1)):return False
    return True

def indoor_floors(blocks,plan,point,legacy=False):
    is_structure=legacy_structural if legacy else structural
    x,z=point;ceiling=None;solid=0
    for y in range(blocks.top(x,z),-63,-1):
        name=blocks.get(x,y,z)
        if name=='minecraft:air':solid=0;continue
        solid+=1
        # Thick gabled roofs are still above valid upper rooms. Only stop in
        # natural ground; the legacy scan retains its original cutoff for IDs.
        if solid>=8 and (legacy or not structural(name)):break
        if support(name) and is_structure(name) and ceiling is not None and 3<=ceiling-y<=18:
            if blocks.get(x,y+1,z)=='minecraft:air' and blocks.get(x,y+2,z)=='minecraft:air' and enclosed(blocks,plan,x,y+1,z,legacy=legacy):
                if not any(re.search(r'(?:chest|barrel|door)$',blocks.get(x+dx,y+1,z+dz)) for dx,dz in DIRECTIONS):yield [x,y+1,z]
        ceiling=y if is_structure(name) else None

def indoor(blocks,plan,point):
    if 'floor_y' not in plan:return next(indoor_floors(blocks,plan,point),None)
    x,z=point;y=plan['floor_y']
    if blocks.get(x,y,z)!='minecraft:air' or blocks.get(x,y+1,z)!='minecraft:air' or not support(blocks.get(x,y-1,z)):return None
    if not enclosed(blocks,plan,x,y,z,roof=True):return None
    if any(re.search(r'(?:chest|barrel|door)$',blocks.get(x+dx,y,z+dz)) for dx,dz in DIRECTIONS):return None
    return [x,y,z]

def generated_chests(blocks,plan):
    found={};seen=set()
    for x,z in plan['points']:
        try:key,entities=blocks.block_entities(x,z)
        except MissingChunk:continue
        if key in seen:continue
        seen.add(key)
        for entity in entities:
            body=entity.compound;identity=str(body.get('MapCraftFurnitureId',''))
            if str(body.get('id',''))!='Chest':continue
            if identity!=plan['id'] and not identity.startswith(plan['id']+'/floor/'):continue
            pos=[int(body[k]) for k in ('x','y','z')];pos[0]+=blocks.offset[0];pos[2]+=blocks.offset[1]
            if blocks.get(*pos)=='minecraft:chest':found[identity]=pos
    return found

def floor_plans(blocks,plan,counts):
    if plan.get('placement')!='per_floor':return [plan]
    found={};existing=generated_chests(blocks,plan)
    # Retain the original top/first indoor floor's ID and draw. This preserves
    # old generator chests without refilling them or biasing that floor's odds.
    anchor=existing.get(plan['id'],[None,None,None])[1]
    if anchor is None:
        for point in plan['points']:
            try:previous=next(indoor_floors(blocks,plan,point,legacy=True),None)
            except MissingChunk:continue
            if previous is not None:anchor=previous[1];break
    for point in plan['points']:
        try:
            for x,y,z in indoor_floors(blocks,plan,point):
                if anchor is None:anchor=y
                found.setdefault(y,[]).append([x,z])
        except MissingChunk:counts['incomplete']+=1
    for position in existing.values():found.setdefault(position[1],[]).append([position[0],position[2]])
    counts['floors_detected']+=len(found);result=[]
    for y,points in sorted(found.items()):
        identity=plan['id'] if y==anchor else plan['id']+'/floor/'+str(y)
        digest=bytes.fromhex(plan['id']) if y==anchor else hashlib.sha256(identity.encode()).digest()
        if int.from_bytes(digest[:4],'big')/2**32>=plan['probability']:continue
        counts['floors_selected']+=1
        seed=plan['seed'] if y==anchor else int.from_bytes(digest[12:16],'big') or 1
        result.append({**plan,'id':identity,'floor_y':y,'points':points,'seed':seed,'existing_position':existing.get(identity)})
    return result

def bed_clear(blocks,plan,foot,direction):
    x,y,z=foot;dx,dz=DIRECTIONS[direction];head=[x+dx,y,z+dz]
    for px,py,pz in [foot,head]:
        if blocks.get(px,py,pz)!='minecraft:air' or blocks.get(px,py+1,pz)!='minecraft:air' or not support(blocks.get(px,py-1,pz)):return None
        if any(furniture(blocks.get(px+a,py,pz+b)) for a,b in DIRECTIONS):return None
    if not any(all(blocks.get(px+dz*side,py,pz-dx*side)=='minecraft:air' and blocks.get(px+dz*side,py+1,pz-dx*side)=='minecraft:air' and support(blocks.get(px+dz*side,py-1,pz-dx*side)) for px,py,pz in [foot,head]) for side in [-1,1]):return None
    return head if enclosed(blocks,plan,*head,roof=True) else None

def bake_directory(world,chests,beds,*,offset=(0,0),state_path=None,owned_bounds=None,notify=print,database=None):
    world=Path(world);state_path=Path(state_path) if state_path else world/'mapcraft_furniture-state.sqlite'
    journal=sqlite3.connect(state_path);journal.execute('PRAGMA journal_mode=WAL')
    journal.execute('CREATE TABLE IF NOT EXISTS plans(kind TEXT,id TEXT,status TEXT,data TEXT,PRIMARY KEY(kind,id))')
    journal.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT)')
    schema=journal.execute("SELECT value FROM meta WHERE key='furniture_schema'").fetchone()
    if schema is None or int(schema[0])<VERSION:
        # A previous indoor scan may have rejected a thick roof. Retry those
        # empty locations, while retaining every placed/recovered inventory.
        journal.execute("DELETE FROM plans WHERE kind='chest' AND status IN ('skipped','blocked')")
        journal.execute("INSERT OR REPLACE INTO meta VALUES('furniture_schema',?)",(str(VERSION),))
    journal.commit()
    owns_database=database is None
    db=leveldb.LevelDB(str(world/'db'),create_if_missing=False) if owns_database else database
    blocks=Blocks(db,offset);counts={'chests':0,'beds':0,'skipped':0,'existing':0,'incomplete':0,'floors_detected':0,'floors_selected':0}
    pool=json.loads((BASE/'survival-items.json').read_text(encoding='utf-8'))['items']
    try:
        def expand():
            for plan in chests:yield from floor_plans(blocks,plan,counts)
        for kind,plans in [('chest',expand()),('bed',beds)]:
            for number,plan in enumerate(plans):
                record=journal.execute('SELECT status,data FROM plans WHERE kind=? AND id=?',(kind,plan['id'])).fetchone()
                if record and record[0] not in ('placing','incomplete'):
                    counts['existing']+=1
                    if record[0] in ('placed','recovered'):counts['chests' if kind=='chest' else 'beds']+=1
                    continue
                if kind=='chest' and plan.get('existing_position'):
                    journal.execute('INSERT OR REPLACE INTO plans VALUES(?,?,?,?)',(kind,plan['id'],'recovered',json.dumps({'position':plan['existing_position']})))
                    journal.commit();counts['existing']+=1;counts['chests']+=1;continue
                position=None;direction=None;head=None;missing=False
                def owned(p):
                    return owned_bounds is None or (owned_bounds[0]<=p[0]<owned_bounds[2] and owned_bounds[1]<=p[2]<owned_bounds[3])
                if record and record[0]=='placing':
                    data=json.loads(record[1]);position=data['position'];direction=data.get('direction')
                    # A crash after the LevelDB batch must never refill or create
                    # a second container, even if its inventory is now empty.
                    if blocks.get(*position)!='minecraft:air':
                        journal.execute("UPDATE plans SET status='recovered' WHERE kind=? AND id=?",(kind,plan['id']));journal.commit();counts['existing']+=1;counts['chests' if kind=='chest' else 'beds']+=1;continue
                else:
                    for point in plan['points']:
                        try:
                            candidate=indoor(blocks,plan,point)
                            if candidate is None or not owned(candidate):continue
                            if kind=='chest':position=candidate;break
                            for i in range(4):
                                direction=(plan['seed']+i)%4;head=bed_clear(blocks,plan,candidate,direction)
                                if head is not None and owned(head):position=candidate;break
                            if position is not None:break
                        except MissingChunk:missing=True
                if position is None:
                    state='incomplete' if missing else 'skipped';counts[state]+=1
                    journal.execute('INSERT OR REPLACE INTO plans VALUES(?,?,?,?)',(kind,plan['id'],state,'{}'));journal.commit();continue
                x,y,z=position
                if blocks.get(x,y+1,z)!='minecraft:air' or not support(blocks.get(x,y-1,z)):
                    journal.execute('INSERT OR REPLACE INTO plans VALUES(?,?,?,?)',(kind,plan['id'],'blocked','{}'));journal.commit();counts['skipped']+=1;continue
                if kind=='bed':
                    head=bed_clear(blocks,plan,position,direction)
                    if head is None:counts['skipped']+=1;continue
                if blocks.has_entity(*position) or (head is not None and blocks.has_entity(*head)):
                    journal.execute('INSERT OR REPLACE INTO plans VALUES(?,?,?,?)',(kind,plan['id'],'blocked','{}'));journal.commit();counts['skipped']+=1;continue
                # Write-ahead intent is committed before any native block changes.
                journal.execute('INSERT OR REPLACE INTO plans VALUES(?,?,?,?)',(kind,plan['id'],'placing',json.dumps({'position':position,'direction':direction})));journal.commit()
                if kind=='chest':
                    blocks.set(x,y,z,'minecraft:chest',{'minecraft:cardinal_direction':nbt.StringTag('north')})
                    blocks.entity(x,y,z,'Chest',plan['id'],{'Findable':nbt.ByteTag(0),'Items':nbt.ListTag([item_nbt(item) for item in loot_for(plan,pool)])})
                    counts['chests']+=1
                else:
                    for p,is_head in [(position,False),(head,True)]:
                        blocks.set(*p,'minecraft:bed',{'direction':nbt.IntTag(direction),'head_piece_bit':nbt.ByteTag(is_head),'occupied_bit':nbt.ByteTag(0)})
                        blocks.entity(*p,'Bed',plan['id'],{'color':nbt.ByteTag(14)})
                    counts['beds']+=1
                blocks.commit()
                journal.execute("UPDATE plans SET status='placed' WHERE kind=? AND id=?",(kind,plan['id']));journal.commit()
                if number%100==0:notify(f'家具を事前配置: 宝箱 {counts["chests"]} / ベッド {counts["beds"]}')
    finally:
        if owns_database:db.close()
        journal.close()
    report={'version':VERSION,'mode':'native_blocks','target_bedrock':'1.26.60+','counts':counts}
    (world/'mapcraft_furniture.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
    return report

def strip_packs(archive,output):
    retained={}
    if any(name.startswith('behavior_packs/') and any('/'+part+'/' in name for part in ('dimensions','blocks','entities','items')) for name in archive.namelist()):
        raise ValueError('高さ拡張や独自ブロックを含むワールドは完全なパックなし版へ変換できません。通常の高さで再生成してください。')
    for item in archive.infolist():
        name=item.filename
        if name.startswith(('behavior_packs/','resource_packs/')):
            if '/map_data/' in name or Path(name).name.startswith(('LICENSE','CREDITS','UPSTREAM','OSM-CREDITS','THIRD-PARTY')):
                retained['mapcraft_credits/'+name]=archive.read(item)
            continue
        if name in ('world_behavior_packs.json','world_resource_packs.json'):continue
        with archive.open(item) as src,output.open(item,'w') as dst:
            while block:=src.read(1024*1024):dst.write(block)
    for name,data in retained.items():output.writestr(name,data)

def bake_archive(path,chests,beds,*,vanilla=False,reference=None,notify=print,villagers=None,ecology=None):
    path=Path(path).resolve()
    with tempfile.TemporaryDirectory(prefix='.furniture-',dir=path.parent) as temporary:
        root=Path(temporary)
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                if not (root/item.filename).resolve().is_relative_to(root):raise ValueError('Invalid archive member path')
            archive.extractall(root)
        marker=root/'mapcraft_furniture.json'
        previous=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
        if previous.get('version')==VERSION:report=previous
        else:
            report=bake_directory(root,chests,[] if previous.get('version',0)>=1 else beds,notify=notify)
            if previous.get('version',0)>=1:
                report['counts']['beds']=previous.get('counts',previous.get('totals',{})).get('beds',0)
                marker.write_text(json.dumps(report),encoding='utf-8')
        if villagers is not None and villagers['options']['enabled']:
            from villagers_backend import bake_villagers
            report={**report,'villagers':bake_villagers(root,villagers,notify=notify)}
        if ecology is not None:
            from ecology_backend import bake_mobs
            report={**report,'ecology':bake_mobs(root,ecology,notify=notify)}
        for pattern in ('mapcraft_furniture-state.sqlite*','mapcraft_villagers-state.sqlite*','mapcraft_ecology-state.sqlite*'):
            for file in root.glob(pattern):file.unlink()
        tracking=root/'mapcraft_tracking.json'
        if tracking.exists() or reference is not None:
            data=asdict(reference) if reference is not None else json.loads(tracking.read_text(encoding='utf-8'))
            data['furniture_baked']=True;tracking.write_text(json.dumps(data),encoding='utf-8')
        staged=root/'baked.mcworld'
        with zipfile.ZipFile(staged,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as output:
            for file in root.rglob('*'):
                if file.is_file() and file!=staged and file.name!='LOCK':output.write(file,file.relative_to(root).as_posix())
        if vanilla:
            plain=root/'plain.mcworld'
            with zipfile.ZipFile(staged) as archive,zipfile.ZipFile(plain,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as output:strip_packs(archive,output)
            os.replace(plain,path)
        else:os.replace(staged,path)
    return report

def ensure_project_furniture(root,notify=print):
    """Backfill creator tiles before export; this never targets a played world."""
    import national_backend as n
    root=Path(root);config=n.load_project(root);totals={'chests':0,'beds':0,'tiles':0}
    with n.connect(root/'queue.sqlite') as queue:
        tiles=queue.execute("SELECT tx,tz FROM tiles WHERE state='done' ORDER BY tz,tx").fetchall()
    signature=hashlib.sha256(json.dumps([config['map_id'],config['treasure'],config.get('beds')],sort_keys=True).encode()).hexdigest()
    marker=root/'world/mapcraft_furniture.json'
    if marker.exists():
        previous=json.loads(marker.read_text(encoding='utf-8'))
        if previous.get('version')==VERSION and previous.get('signature')==signature and previous.get('totals',{}).get('tiles')==len(tiles):
            if not config.get('furniture_baked') or config.get('furniture_schema')!=VERSION:
                config['furniture_baked']=True;config['furniture_schema']=VERSION;n.atomic_json(root/'project.json',config)
            return previous
    for index,(tx,tz) in enumerate(tiles):
        path=root/'tile_data'/f'{tx}_{tz}.json';data=json.loads(path.read_text(encoding='utf-8'))
        report=data.get('furniture',{})
        if report.get('version')!=VERSION:
            from treasure import compile_treasure
            ref=MapReference(config['map_id'],tuple(config['bbox']),config['scale'],1,1,projection=config['projection'],origin=tuple(config['origin']),
                             treasure_enabled=config['treasure']['enabled'],treasure_probability=config['treasure']['probability'],
                             beds_enabled=config.get('beds',{}).get('enabled',True),beds_probability=config.get('beds',{}).get('probability',.25))
            payload=None
            if data['treasure'].get('version',1)<3:
                source=n.connect(config['index'])
                try:payload=n.extract(source,data['bbox'])
                finally:source.close()
                treasure=compile_treasure(ref,payload);size=config['tile']
                treasure['plans']=[p for p in treasure['plans'] if tx*size<=p['points'][0][0]<(tx+1)*size and tz*size<=p['points'][0][1]<(tz+1)*size]
                treasure['selected']=len(treasure['plans']);data['treasure']=treasure
            if data.get('beds') is None:
                from beds import compile_beds
                if payload is None:
                    source=n.connect(config['index'])
                    try:payload=n.extract(source,data['bbox'])
                    finally:source.close()
                beds=compile_beds(ref,payload);size=config['tile']
                beds['plans']=[p for p in beds['plans'] if tx*size<=p['points'][0][0]<(tx+1)*size and tz*size<=p['points'][0][1]<(tz+1)*size]
                beds['selected']=len(beds['plans']);data['beds']=beds
            old_beds=report.get('counts',{}).get('beds',0);beds_done=report.get('version',0)>=1
            report=bake_directory(root/'world',data['treasure']['plans'],[] if beds_done else data.get('beds',{}).get('plans',[]),
                                  state_path=root/'furniture-state.sqlite',notify=notify)
            if beds_done:report['counts']['beds']=old_beds
            data['furniture']=report;n.atomic_json(path,data)
        totals['chests']+=report['counts']['chests'];totals['beds']+=report['counts']['beds'];totals['tiles']+=1
        if index%20==0:notify(f'生成済み区画の家具: {index+1:,}/{len(tiles):,} 区画')
    summary={'version':VERSION,'mode':'native_blocks','treasure_unit':'floor','totals':totals,'signature':signature}
    n.atomic_json(root/'world/mapcraft_furniture.json',summary)
    config['furniture_baked']=True;config['furniture_schema']=VERSION;n.atomic_json(root/'project.json',config)
    return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);args=parser.parse_args()
    request=json.loads(Path(args.request).read_text(encoding='utf-8'))
    reference=MapReference.load(request['reference']) if 'reference' in request else None
    if 'plans' in request:plans=request['plans']
    else:
        from treasure import compile_treasure
        from beds import compile_beds
        payload=json.loads(Path(request['osm']).read_text(encoding='utf-8'))
        plans={'chests':compile_treasure(reference,payload)['plans'],'beds':compile_beds(reference,payload)['plans']}
    villagers=None
    if (request.get('villagers') or {}).get('enabled'):
        from villagers import compile_villagers
        payload=json.loads(Path(request['osm']).read_text(encoding='utf-8'))
        boundaries=json.loads(Path(request['boundaries']).read_text(encoding='utf-8'))
        villagers=compile_villagers(reference,payload,boundaries,request['villagers'],notify=print)
    ecology=None
    if request.get('mobs') is not None:
        from ecology import compile_mobs
        payload=json.loads(Path(request['osm']).read_text(encoding='utf-8'))
        ecology=compile_mobs(reference,payload,request['mobs'])
    print(json.dumps(bake_archive(request['world'],plans['chests'],plans['beds'],vanilla=request.get('vanilla',False),reference=reference,villagers=villagers,ecology=ecology),ensure_ascii=True),flush=True)
