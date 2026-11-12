"""Correct existing open water from real bathymetry in private Bedrock output."""
import argparse
from contextlib import closing
from dataclasses import asdict
from functools import lru_cache
import hashlib
from itertools import groupby
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import zipfile
BASE=Path(__file__).resolve().parent;sys.path.insert(0,str(BASE))
import amulet_nbt as nbt
import leveldb
import numpy as np
from bathymetry import Survey,options,CREDITS
from geology_backend import decode,encode
from tracking import MapReference

WATER={'minecraft:water','minecraft:flowing_water'}
NATURAL={'stone','dirt','grass','grass_block','sand','red_sand','gravel','clay','mud','sandstone','red_sandstone','deepslate',
    'andesite','granite','diorite','tuff','calcite','basalt','terracotta','coarse_dirt','rooted_dirt','podzol','moss_block'}

def freshwater_mask(reference,payload):
    from shapely.geometry import Polygon,LineString,Point
    from shapely.ops import unary_union
    from shapely.prepared import prep
    from municipalities import join_rings
    nodes={e['id']:e for e in payload.get('elements',[]) if e.get('type')=='node' and 'lat' in e};shapes=[]
    for element in payload.get('elements',[]):
        tags=element.get('tags',{})
        if tags.get('water') in ('sea','ocean') or tags.get('natural')=='coastline':continue
        if tags.get('natural')!='water' and tags.get('waterway') not in ('river','stream','canal','riverbank'):continue
        if element.get('type')=='relation':
            outer=[];inner=[]
            for member in element.get('members',[]):
                points=[reference.project(p['lat'],p['lon']) for p in member.get('geometry',[]) if p and 'lat' in p]
                if points:(inner if member.get('role')=='inner' else outer).append(points)
            try:
                area=unary_union([Polygon(r).buffer(0) for r in join_rings(outer)])
                if inner:area=area.difference(unary_union([Polygon(r).buffer(0) for r in join_rings(inner)]))
                if not area.is_empty:shapes.append(area)
            except ValueError:pass
            continue
        if element.get('type')!='way':continue
        geom=element.get('geometry') or [nodes[i] for i in element.get('nodes',[]) if i in nodes]
        points=[reference.project(p['lat'],p['lon']) for p in geom if p and 'lat' in p]
        if len(points)>=4 and points[0]==points[-1]:shapes.append(Polygon(points).buffer(0))
        elif len(points)>=2:shapes.append(LineString(points).buffer(8*reference.scale))
    if not shapes:return lambda x,z:False
    area=prep(unary_union(shapes));return lambda x,z:area.covers(Point(x,z))

def apply_directory(world,reference,cache,settings=None,notify=print,*,survey=None,freshwater=None,state_path=None,coverage=None,database=None,publish=True):
    world=Path(world);settings=options(settings)
    if not settings['enabled']:return None
    signature=hashlib.sha256(json.dumps([1,asdict(reference),settings],sort_keys=True).encode()).hexdigest()
    marker=world/'mapcraft_bathymetry.json'
    if marker.exists():
        previous=json.loads(marker.read_text(encoding='utf-8'))
        if previous.get('signature')==signature and (coverage is None or previous.get('coverage')==coverage):return previous
    survey=survey or Survey(cache,notify);freshwater=freshwater or (lambda x,z:False)
    state_path=Path(state_path or world/'mapcraft_bathymetry-state.sqlite')
    journal=sqlite3.connect(state_path);journal.execute('CREATE TABLE IF NOT EXISTS chunks(x INTEGER,z INTEGER,signature TEXT,PRIMARY KEY(x,z))');journal.commit()
    db=None;counts={'chunks':0,'changed_columns':0,'clipped_columns':0,'protected_columns':0,'missing_depth':0};pending={};completed=[]
    try:
        db=database if database is not None else leveldb.LevelDB(str(world/'db'),create_if_missing=False)
        def flush():
            nonlocal pending,completed
            if pending:db.putBatch(dict(sorted(pending.items())))
            journal.executemany('INSERT OR REPLACE INTO chunks VALUES(?,?,?)',[(x,z,signature) for x,z in completed]);journal.commit()
            pending={};completed=[]
        records=((key,value) for key,value in db.iterate() if len(key) in (9,10))
        for prefix,group in groupby(records,key=lambda item:item[0][:8]):
            raw=dict(group);cx,cz=struct.unpack('<ii',prefix)
            if prefix+bytes([43]) not in raw or not any(b'minecraft:water' in value or b'minecraft:flowing_water' in value for key,value in raw.items() if key[8]==47):continue
            if journal.execute('SELECT 1 FROM chunks WHERE x=? AND z=? AND signature=?',(cx,cz,signature)).fetchone():continue
            heights=np.frombuffer(raw[prefix+bytes([43])][:512],dtype='<i2').reshape(16,16).T
            sections={};dirty=set()
            def section(y):
                sy=y//16
                if sy not in sections:
                    key=prefix+struct.pack('<Bb',47,sy);value=raw.get(key)
                    if value is None:
                        air=nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag('minecraft:air'),'states':nbt.CompoundTag({}),'version':nbt.IntTag(18168865)}))
                        sections[sy]=(bytes([9,1,sy&255]),[(np.zeros(4096,dtype=np.uint32),[air])])
                    else:sections[sy]=(value[:3],decode(value))
                return sy,sections[sy][1][0]
            def name(x,y,z):
                _,(indices,palette)=section(y);return str(palette[int(indices[x*256+z*16+y%16])].compound['name'])
            def put(x,y,z,material):
                sy,(indices,palette)=section(y);index=x*256+z*16+y%16
                if str(palette[int(indices[index])].compound['name'])==material:return
                states={'liquid_depth':nbt.IntTag(0)} if material=='minecraft:water' else {}
                slot=next((i for i,v in enumerate(palette) if str(v.compound['name'])==material and v.compound['states']==nbt.CompoundTag(states)),None)
                if slot is None:
                    slot=len(palette);palette.append(nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag(material),'states':nbt.CompoundTag(states),'version':palette[0].compound['version']})))
                indices[index]=slot;dirty.add(sy)
            surfaces=np.full((16,16),-1,dtype=np.int16)
            for x in range(16):
                for z in range(16):
                    top=min(510,int(heights[x,z])+1);sea=None
                    for y in range(top,max(2,top-16),-1):
                        value=name(x,y,z)
                        if value in WATER:sea=y;break
                        if value!='minecraft:air':break
                    if sea is not None and sea>=6:surfaces[x,z]=sea
            # Small artificial pools (including guardian arenas) are not seas.
            if np.count_nonzero(surfaces>=0)<128:continue
            for x in range(16):
                for z in range(16):
                    sea=int(surfaces[x,z])
                    if sea<0:continue
                    wx,wz=cx*16+x,cz*16+z;lat,lon=reference.geographic(wx,wz)
                    if not reference.contains(wx,wz):continue
                    elevation=survey.elevation(lat,lon)
                    if elevation is None:counts['missing_depth']+=1;continue
                    if elevation>=-1 or freshwater(wx,wz):continue
                    desired=max(2,round(-elevation*reference.scale));depth=min(desired,settings['max_depth'],sea-3);floor=sea-depth
                    old=sea
                    while old>1 and name(x,old,z) in WATER:old-=1
                    low=max(1,min(old,floor-2));values=[name(x,y,z) for y in range(low,sea+1)]
                    if any(v not in WATER and v.removeprefix('minecraft:') not in NATURAL and not v.endswith('_ore') for v in values):
                        counts['protected_columns']+=1;continue
                    # Air pockets, structural blocks and bedrock are protected.
                    # Fill a raised seabed completely; never leave trapped voids.
                    for y in range(low,sea+1):
                        material='water' if y>floor else 'sand' if y==floor else 'gravel' if y==floor-1 else 'stone'
                        put(x,y,z,'minecraft:'+material)
                    counts['changed_columns']+=1;counts['clipped_columns']+=int(desired>depth)
            for sy in dirty:pending[prefix+struct.pack('<Bb',47,sy)]=encode(*sections[sy])
            completed.append((cx,cz));counts['chunks']+=1
            if len(completed)>=64:
                flush();notify(f'海底地形を'+('補正' if publish else '計算（一括保存用）')+f': {counts["chunks"]:,}チャンク / {counts["changed_columns"]:,}列')
        flush()
        report={'version':1,'signature':signature,'coverage':coverage,'options':settings,'counts':counts,'source':'NOAA ETOPO 2022 15 arc-second','horizontal_resolution':'approximately 400–500m in Japan, interpolated','maximum_depth_blocks':settings['max_depth'],'preserves_land_and_structures':True}
        if publish:
            marker.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8');(world/'BATHYMETRY-CREDITS.txt').write_text(CREDITS,encoding='utf-8')
        return report
    finally:
        if db is not None and database is None:db.close()
        journal.close()

def apply_archive(path,reference,cache,settings,osm=None,notify=print):
    path=Path(path);temporary=path.with_suffix('.bathymetry-writing')
    with tempfile.TemporaryDirectory(prefix='bathymetry-',dir=path.parent) as folder:
        root=Path(folder)
        with zipfile.ZipFile(path) as archive:archive.extractall(root)
        payload=json.loads(Path(osm).read_text(encoding='utf-8')) if osm and Path(osm).exists() else {}
        report=apply_directory(root,reference,cache,settings,notify,freshwater=freshwater_mask(reference,payload))
        try:
            with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
                for file in root.rglob('*'):
                    if file.is_file() and file.name!='LOCK':archive.write(file,file.relative_to(root).as_posix())
            os.replace(temporary,path)
        finally:temporary.unlink(missing_ok=True)
        return report

def ensure_project_bathymetry(root,notify=print):
    import national_backend as n
    root=Path(root);config=n.load_project(root);settings=options(config.get('bathymetry',{'enabled':False}))
    if not settings['enabled']:return None
    ref=MapReference(config['map_id'],tuple(config['bbox']),config['scale'],1,1,projection=config['projection'],origin=tuple(config['origin']))
    with closing(n.connect(root/'queue.sqlite')) as queue:tiles=queue.execute("SELECT tx,tz FROM tiles WHERE state='done' ORDER BY tz,tx").fetchall()
    coverage=hashlib.sha256(json.dumps(tiles).encode()).hexdigest()
    with closing(n.connect(config['index'])) as source:
        @lru_cache(maxsize=32)
        def mask(tx,tz):return freshwater_mask(ref,n.extract(source,n.tile_spec(tx,tz,config['scale'],config['tile'])['bbox']))
        if os.environ.get('MAPCRAFT_BATHYMETRY_BULK')=='1' or (root/'bulk-bathymetry.enabled').exists():
            from bathymetry_bulk import apply_bulk
            with closing(n.connect(root/'queue.sqlite')) as queue:
                expected_chunks=queue.execute("SELECT COALESCE(SUM(chunks),0) FROM tiles WHERE state='done'").fetchone()[0]
            return apply_bulk(root,ref,root.parent.parent/'cache/bathymetry',settings,notify,
                              freshwater=lambda x,z:mask(x//config['tile'],z//config['tile'])(x,z),coverage=coverage,expected_chunks=expected_chunks)
        return apply_directory(root/'world',ref,root.parent.parent/'cache/bathymetry',settings,notify,
            freshwater=lambda x,z:mask(x//config['tile'],z//config['tile'])(x,z),state_path=root/'bathymetry-state.sqlite',coverage=coverage)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--request',required=True);a=p.parse_args();request=json.loads(Path(a.request).read_text(encoding='utf-8'))
    reference=MapReference(**request['reference'])
    result=apply_archive(request['world'],reference,request['cache'],request['settings'],request.get('osm'))
    print(json.dumps(result,ensure_ascii=False),flush=True)
