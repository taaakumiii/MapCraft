"""Bake vanilla Bedrock actors into private creator worlds, with retry journaling."""
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import amulet_nbt as nbt
import leveldb
from furniture_backend import Blocks,MissingChunk,indoor_floors
from villagers import CREDITS
from actor_batch import BufferedDB

def storage_key(identity):
    # Bedrock's actor storage transforms the UniqueID, then writes big-endian.
    value=(0x100000000+((identity&0xffffffffffffffff)^0xffffffff00000000))&0xffffffffffffffff
    return struct.pack('>Q',value)

def actor(identity,position,plan):
    floats=lambda values:nbt.ListTag([nbt.FloatTag(v) for v in values])
    definitions=['+minecraft:villager_v2','+adult','+make_and_receive_love','+unskilled','+behavior_peasant','+basic_schedule','+villager_skin_'+str(plan['seed']%6)]
    return nbt.NamedTag(nbt.CompoundTag({
        'identifier':nbt.StringTag('minecraft:villager_v2'),'UniqueID':nbt.LongTag(identity),
        'Pos':floats(position),'Motion':floats([0,0,0]),'Rotation':floats([plan['seed']%360,0]),
        'Persistent':nbt.ByteTag(1),'OnGround':nbt.ByteTag(1),'IsBaby':nbt.ByteTag(0),'Age':nbt.IntTag(0),
        'Variant':nbt.IntTag(0),'MarkVariant':nbt.IntTag(0),'SkinID':nbt.IntTag(plan['seed']%6),
        'Health':nbt.ShortTag(20),'Air':nbt.ShortTag(300),'MaxAir':nbt.ShortTag(300),
        'Fire':nbt.ShortTag(0),'FallDistance':nbt.FloatTag(0),'Invulnerable':nbt.ByteTag(0),
        'definitions':nbt.ListTag([nbt.StringTag(v) for v in definitions]),
        'Attributes':nbt.ListTag([nbt.CompoundTag({'Name':nbt.StringTag('minecraft:health'),'Base':nbt.FloatTag(20),'Current':nbt.FloatTag(20),'Max':nbt.FloatTag(20),'Min':nbt.FloatTag(0)})]),
        'MapCraftVillagerId':nbt.StringTag(plan['id'])}))

def resident_position(blocks,plan,outdoors,used):
    interior=[]
    for point in plan['points']:
        try:interior.extend(indoor_floors(blocks,plan,point))
        except MissingChunk:continue
    # Do not hide every resident on the top floor of a tower.
    interior.sort(key=lambda p:(p[1],p[0],p[2]))
    exterior=[]
    if outdoors:
        from ecology_backend import surface
        w,n,e,s=plan['bounds'];cx=(w+e)//2;cz=(n+s)//2
        points=[(int(w)-2,int(cz)),(int(e)+2,int(cz)),(int(cx),int(n)-2),(int(cx),int(s)+2),
                (int(w)-2,int(n)-2),(int(e)+2,int(s)+2),(int(w)-2,int(s)+2),(int(e)+2,int(n)-2)]
        start=int(plan['id'][:8],16)%len(points)
        for x,z in points[start:]+points[:start]:
            position=surface(blocks,[x,z],{'type':'minecraft:villager_v2','width':.9})
            if position:exterior.append(position)
    choices=exterior+interior if outdoors and plan['seed']%3==0 else interior+exterior
    return next((p for p in choices if all((p[0]+dx,p[2]+dz) not in used for dx in (-1,0,1) for dz in (-1,0,1))),None)

def bake_villagers(world,compiled,*,state_path=None,notify=print):
    world=Path(world);state_path=Path(state_path or world/'mapcraft_villagers-state.sqlite')
    signature=hashlib.sha256(json.dumps(compiled,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    marker=world/'mapcraft_villagers.json'
    if marker.exists():
        previous=json.loads(marker.read_text(encoding='utf-8'))
        if previous.get('signature')==signature:
            notify('配置済みの村人データを再利用します。')
            return {**previous,'counts':{'placed':0,'existing':previous['counts']['placed']+previous['counts']['existing'],'blocked':previous['counts']['blocked']}}
    journal=sqlite3.connect(state_path)
    journal.execute('CREATE TABLE IF NOT EXISTS residents(id TEXT PRIMARY KEY,status TEXT,actor_key BLOB)');journal.commit()
    counts={'placed':0,'existing':0,'blocked':0};db=None
    try:
        db=BufferedDB(leveldb.LevelDB(str(world/'db'),create_if_missing=False));blocks=Blocks(db,(0,0));used=set();staged=[]
        def flush():
            db.flush()
            journal.executemany("UPDATE residents SET status='placed' WHERE id=?",[(identity,) for identity in staged]);journal.commit();staged.clear()
        if not journal.execute('SELECT 1 FROM residents LIMIT 1').fetchone():
            for actor_key,data in db.iterate():
                if not actor_key.startswith(b'actorprefix'):continue
                body=nbt.load(data,compressed=False,little_endian=True).compound
                identity=str(body.get('MapCraftVillagerId',''))
                if identity:journal.execute('INSERT OR IGNORE INTO residents VALUES(?,?,?)',(identity,'placed',actor_key[len(b'actorprefix'):]))
            journal.commit()
        sequence=int.from_bytes(blocks.value(b'mapcraft_villager_sequence') or b'\0','little')
        for plan in compiled['plans']:
            previous=journal.execute('SELECT status,actor_key FROM residents WHERE id=?',(plan['id'],)).fetchone()
            if previous:
                # Completed residents are not re-created after death or movement.
                if previous[0]!='placing' or blocks.value(b'actorprefix'+previous[1]) is not None:
                    journal.execute("UPDATE residents SET status='placed' WHERE id=? AND status='placing'",(plan['id'],));journal.commit();counts['existing']+=1;continue
            position=resident_position(blocks,plan,compiled['options'].get('outdoors_enabled',False),used)
            if position is None:counts['blocked']+=1;continue
            if previous:key=bytes(previous[1]);identity=((int.from_bytes(key,'big')-0x100000000)^0xffffffff00000000)&0xffffffffffffffff;identity=identity-2**64 if identity>=2**63 else identity
            else:
                while True:
                    sequence+=1
                    if sequence>=2**32:raise ValueError('村人の保存IDを確保できません。')
                    identity=-2**32+sequence;key=storage_key(identity)
                    if blocks.value(b'actorprefix'+key) is None:break
            sequence=max(sequence,identity+2**32)
            x,y,z=position;digest_key=b'digp'+struct.pack('<ii',x//16,z//16)
            used.add((x,z))
            digest=blocks.value(digest_key) or b''
            if len(digest)%8:raise ValueError('既存のActor参照が壊れています。村人配置を中止します。')
            journal.execute('INSERT OR REPLACE INTO residents VALUES(?,?,?)',(plan['id'],'placing',key));journal.commit()
            record=actor(identity,[x+.5,y,z+.5],plan).save_to(compressed=False,little_endian=True)
            batch={b'actorprefix'+key:record,digest_key:digest+key,b'mapcraft_villager_sequence':sequence.to_bytes(4,'little')}
            version_key=struct.pack('<iiB',x//16,z//16,44);version=blocks.value(version_key)
            if version is None or version[0]<40:batch[version_key]=bytes([40])
            db.putBatch(batch)
            staged.append(plan['id']);counts['placed']+=1
            if len(staged)>=128:flush()
        flush()
        report={'version':1,'signature':signature,'mode':'native_vanilla_actors','counts':counts,'options':compiled['options'],'areas':compiled.get('areas',[]),'unmatched':compiled.get('unmatched',[])}
        (world/'mapcraft_villagers.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        (world/'POPULATION-CREDITS.txt').write_text(CREDITS,encoding='utf-8')
        notify(f'村人を事前配置: 新規{counts["placed"]:,}人 / 配置済み{counts["existing"]:,}人 / 安全な屋内なし{counts["blocked"]:,}人')
        return report
    finally:
        if db is not None:db.close()
        journal.close()

def ensure_project_villagers(root,notify=print):
    import national_backend as n
    from tracking import MapReference
    from villagers import options,compile_villagers
    root=Path(root);config=n.load_project(root);settings=options(config.get('villagers'))
    if not settings['enabled']:return
    ref=MapReference(config['map_id'],tuple(config['bbox']),config['scale'],1,1,projection=config['projection'],origin=tuple(config['origin']))
    with n.connect(config['index']) as source:
        towns=[n.unpacked(row[0]) for row in source.execute('SELECT data FROM towns')]
        prefs=[n.unpacked(row[0]) for row in source.execute('SELECT data FROM prefectures')]
        with n.connect(root/'queue.sqlite') as queue:
            tiles=queue.execute("SELECT tx,tz FROM tiles WHERE state='done' ORDER BY tz,tx").fetchall()
        totals={'tiles':0,'placed':0,'existing':0,'blocked':0};unmatched=set()
        for i,(tx,tz) in enumerate(tiles):
            size=config['tile'];bounds=(tx*size,tz*size,(tx+1)*size,(tz+1)*size)
            path=root/'tile_data'/f'{tx}_{tz}.json';tile=json.loads(path.read_text(encoding='utf-8'))
            signature={'options':settings,'population_as_of':'2026-01-01','version':1}
            if tile.get('villagers',{}).get('signature')==signature:report=tile['villagers']['report']
            else:
                from parallel_plans import cached
                prepared=cached(root,config,tile,path.name)
                if prepared and prepared.get('villagers',{}).get('options')==settings:compiled=prepared['villagers']
                else:compiled=compile_villagers(ref,n.extract(source,tile['bbox']),towns,settings,prefectures=prefs,owned_bounds=bounds,notify=notify)
                report=bake_villagers(root/'world',compiled,state_path=root/'villagers-state.sqlite',notify=notify)
                tile['villagers']={'signature':signature,'report':report};n.atomic_json(path,tile)
            totals['tiles']+=1
            for kind in ('placed','existing','blocked'):totals[kind]+=report['counts'][kind]
            unmatched.update(report.get('unmatched',[]))
            if i%20==0:notify(f'村人を生成: {i+1:,}/{len(tiles):,} 区画')
        n.atomic_json(root/'world/mapcraft_villagers.json',{'version':1,'mode':'native_vanilla_actors','totals':totals,'options':settings,'unmatched':sorted(unmatched)})
