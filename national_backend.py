"""Offline PBF index and crash-resumable, fixed-frame Bedrock world builder.

Runs in the bundled Python 3.12 runtime (Mojang LevelDB has no 3.13 wheel).
The editable database is a private build output, never an installed play world.
"""
from contextlib import contextmanager
from dataclasses import asdict
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import uuid
import zipfile
import zlib

R=6371000.
ORIGIN=(35.,135.)
TILE=1024
FORMAT=1
TOKYO_SPAWN=(35.681236,139.765800)  # West of Tokyo Station, near the Marunouchi forecourt.
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))  # embedded Python deliberately ignores script dirs
from national_state import index_info,empty_index,project_info
ENGINE=BASE/'engine/arnis-windows.exe'
SOURCE='https://download.geofabrik.de/asia/japan-latest.osm.pbf'
SCOPE='https://download.geofabrik.de/asia/japan.poly'

def emit(kind,**values):
    print(json.dumps({'event':kind,**values},ensure_ascii=True),flush=True)

def merc_y(lat):return R*math.log(math.tan(math.pi/4+math.radians(lat)/2))

def forward(lat,lon,scale):
    k=scale*math.cos(math.radians(ORIGIN[0]))
    return R*math.radians(lon-ORIGIN[1])*k,-(merc_y(lat)-merc_y(ORIGIN[0]))*k

def inverse(x,z,scale):
    k=scale*math.cos(math.radians(ORIGIN[0]))
    return math.degrees(2*math.atan(math.exp((merc_y(ORIGIN[0])-z/k)/R))-math.pi/2),ORIGIN[1]+math.degrees(x/(R*k))

def tile_spec(tx,tz,scale,tile=TILE):
    # Generate with a padding halo, then keep ONLY this tile's own chunks.
    # The engine centres its projection at the arithmetic bbox centre. Make
    # that centre exact, and compensate its cos(latitude) scale coefficient.
    ox,oz=tx*tile+tile//2,tz*tile+tile//2
    lat,lon=inverse(ox,oz,scale)
    north,west=inverse(tx*tile-32,tz*tile-32,scale)
    south,east=inverse((tx+1)*tile+32,(tz+1)*tile+32,scale)
    dy=max(north-lat,lat-south)+1e-9
    dx=max(lon-west,east-lon)+1e-9
    return {'bbox':(lat-dy,lon-dx,lat+dy,lon+dx),'offset':(ox,oz),
            'engine_scale':scale*math.cos(math.radians(ORIGIN[0]))/math.cos(math.radians(lat))}

def connect(path):
    db=sqlite3.connect(path,timeout=30)
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    return db

def atomic_json(path,data):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,path)

def packed(data):return zlib.compress(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode(),3)
def unpacked(data):return json.loads(zlib.decompress(data))

@contextmanager
def project_lock(root):
    import msvcrt
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with (root/'builder.lock').open('a+b') as lock:
        if lock.tell()==0:lock.write(b'0');lock.flush()
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('この全国プロジェクトは別の生成処理が使用中です。')
        try:yield
        finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

def index_pbf(source,dest,cancel=None):
    """One sequential pass, disk node-location cache, compressed R-tree index."""
    import osmium
    source=Path(source).resolve();dest=Path(dest)
    if not source.is_file() or not source.name.endswith('.pbf') or not source.stat().st_size:
        raise ValueError('保存が完了した日本の .osm.pbf ファイルを選んでください。.part ファイルはダウンロード中です。')
    if dest.exists() and empty_index(dest):
        backup=dest.with_name(dest.name+'.empty-'+str(time.time_ns())+'.bak')
        os.replace(dest,backup)
        emit('log',text='空の索引ファイルを退避し、全国データの索引を作成します。')
    if dest.exists():
        info=index_info(dest)
        if info.get('complete')=='1' and info.get('source_size')==str(source.stat().st_size) and info.get('source_mtime')==str(source.stat().st_mtime_ns) and info.get('source')==str(source):
            emit('log',text='全国データの索引は作成済みです。');return
        raise ValueError('別のデータ、または作成途中の索引です。新しい索引保存先を指定してください。')
    dest.parent.mkdir(parents=True,exist_ok=True);partial=dest.with_suffix('.building.sqlite')
    # An interrupted PBF scan is restarted; committed generation tiles resume.
    for suffix in ('','-wal','-shm'):
        p=Path(str(partial)+suffix)
        if p.exists():p.unlink()
    db=connect(partial)
    db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);'
        'CREATE TABLE features(rowid INTEGER PRIMARY KEY,kind TEXT,osm_id INTEGER,data BLOB,UNIQUE(kind,osm_id));'
        'CREATE VIRTUAL TABLE bounds USING rtree(id,w,e,s,n);'
        'CREATE TABLE towns(id INTEGER PRIMARY KEY,data BLOB);'
        'CREATE TABLE prefectures(code TEXT PRIMARY KEY,data BLOB);')
    count=0;towns=0;bad=[]
    def add(kind,ident,item,points):
        nonlocal count
        if not points:return
        cursor=db.execute('INSERT INTO features(kind,osm_id,data) VALUES(?,?,?)',(kind,ident,packed(item)))
        lat=[p['lat'] for p in points];lon=[p['lon'] for p in points]
        db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(cursor.lastrowid,min(lon),max(lon),min(lat),max(lat)))
        count+=1
        if count%20000==0:
            db.commit();emit('index',features=count,towns=towns)
            if cancel and Path(cancel).exists():raise InterruptedError('索引作成を中止しました。次回は読み込みの最初から再実行します。')
    class Handler(osmium.SimpleHandler):
        def node(self,obj):
            tags=dict(obj.tags)
            if tags and obj.location.valid():add('node',obj.id,{'type':'node','id':obj.id,'tags':tags,'lat':obj.location.lat,'lon':obj.location.lon},[{'lat':obj.location.lat,'lon':obj.location.lon}])
        def way(self,obj):
            pts=[{'lat':p.lat,'lon':p.lon} for p in obj.nodes if p.location.valid()]
            if len(pts)!=len(obj.nodes):return
            add('way',obj.id,{'type':'way','id':obj.id,'tags':dict(obj.tags),'nodes':[p.ref for p in obj.nodes],'geometry':pts},pts)
        def relation(self,obj):
            nonlocal towns
            tags=dict(obj.tags)
            if tags.get('type') not in ('multipolygon','boundary'):return
            members=[];points=[]
            for m in obj.members:
                member={'type':{'w':'way','n':'node','r':'relation'}.get(m.type,m.type),'ref':m.ref,'role':m.role}
                if m.type=='w':
                    row=db.execute('SELECT data FROM features WHERE kind=? AND osm_id=?',('way',m.ref)).fetchone()
                    if row:member['geometry']=unpacked(row[0])['geometry'];points.extend(member['geometry'])
                members.append(member)
            item={'type':'relation','id':obj.id,'tags':tags,'members':members}
            if tags.get('boundary')=='administrative':
                if tags.get('admin_level')=='4' and tags.get('ISO3166-2','').startswith('JP-'):
                    from municipalities import compile_boundaries
                    try:
                        synthetic={**item,'tags':{**tags,'admin_level':'7'}}
                        data=compile_boundaries({'elements':[synthetic]})['municipalities'][0]
                        db.execute('INSERT OR REPLACE INTO prefectures VALUES(?,?)',(tags['ISO3166-2'],packed(data)))
                    except ValueError:pass
                if tags.get('admin_level')=='7':
                    from municipalities import compile_boundaries
                    try:
                        data=compile_boundaries({'elements':[item]})['municipalities'][0]
                        db.execute('INSERT INTO towns VALUES(?,?)',(obj.id,packed(data)));towns+=1
                    except ValueError:bad.append(tags.get('name',str(obj.id)))
                return
            add('relation',obj.id,item,points)
    locations=dest.with_suffix('.node-locations.tmp')
    if locations.exists():locations.unlink()
    try:
        emit('log',text='全国PBFを読み込み中。索引作成中は停止すると読み込みの最初から再実行します。')
        Handler().apply_file(str(source),locations=True,idx='sparse_file_array,'+str(locations))
        prefectures=db.execute('SELECT COUNT(*) FROM prefectures').fetchone()[0]
        for k,v in {'complete':'1','source':str(source),'source_size':str(source.stat().st_size),'source_mtime':str(source.stat().st_mtime_ns),'features':str(count),'towns':str(towns),'prefectures':str(prefectures),'incomplete_towns':json.dumps(bad,ensure_ascii=False)}.items():db.execute('INSERT INTO meta VALUES(?,?)',(k,v))
        db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.close();db=None
        os.replace(partial,dest)
        emit('indexed',features=count,towns=towns,incomplete_towns=bad)
    finally:
        if db is not None:db.close()
        import gc
        gc.collect()
        if locations.exists():
            try:locations.unlink()
            except PermissionError:pass  # libosmium can retain its mmap until exit.

def extract(db,bbox):
    s,w,n,e=bbox
    rows=db.execute('SELECT f.data FROM features f JOIN bounds b ON b.id=f.rowid WHERE b.w<=? AND b.e>=? AND b.s<=? AND b.n>=?',(e,w,n,s))
    elements={};nodes={}
    def way_nodes(item):
        for ident,p in zip(item.get('nodes',[]),item.get('geometry',[])):
            nodes[ident]={'type':'node','id':ident,**p}
    for (blob,) in rows:
        item=unpacked(blob);elements[(item['type'],item['id'])]=item
        if item['type']=='way':way_nodes(item)
        if item['type']=='relation':
            for member in item['members']:
                if member['type']!='way':continue
                row=db.execute('SELECT data FROM features WHERE kind=? AND osm_id=?',('way',member['ref'])).fetchone()
                if row:
                    child=unpacked(row[0]);elements[('way',child['id'])]=child;way_nodes(child)
    for key,node in nodes.items():
        if ('node',key) not in elements:elements[('node',key)]=node
    return {'version':.6,'generator':'MapCraft national PBF index','osm3s':{'copyright':'© OpenStreetMap contributors, ODbL 1.0'},'elements':list(elements.values())}

def read_poly(path):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    lines=iter(Path(path).read_text().splitlines());next(lines);outer=[];holes=[]
    for line in lines:
        line=line.strip()
        if line=='END':break
        ring=[]
        for row in lines:
            if row.strip()=='END':break
            lon,lat=map(float,row.split());ring.append((lon,lat))
        (holes if line.startswith('!') else outer).append(Polygon(ring))
    geometry=unary_union(outer)
    if holes:geometry=geometry.difference(unary_union(holes))
    return geometry

def create_project(root,index,scale,poly,bbox=None,spawn=None,treasure=None,tile=TILE,beds=None,geology=None,villagers=None,mobs=None,include_navigation=False):
    from shapely.geometry import box
    from shapely.ops import transform
    from shapely.prepared import prep
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if (root/'project.json').exists():raise ValueError('この保存先にはプロジェクトが存在します。「再開」を使用してください。')
    if not .3<=scale<=1:raise ValueError('全国モードの縮尺は0.3〜1ブロック/mです。')
    if tile<32 or tile%32:raise ValueError('区画の幅は32の倍数で指定してください。')
    info=index_info(index)
    from villagers import options as villager_options
    villagers=villager_options(villagers)
    from ecology import options as mob_options
    mobs=mob_options(mobs)
    if not isinstance(include_navigation,bool):raise ValueError('ミニマップ設定が不正です。')
    with connect(index) as db:
        prefectures=[unpacked(row[0]) for row in db.execute('SELECT data FROM prefectures')]
    coverage=read_poly(poly)
    if not bbox:
        from shapely.geometry import Polygon,MultiPolygon
        from shapely.ops import unary_union
        if len(prefectures)!=47:raise ValueError('47都道府県の閉じた境界を確認できません。Geofabrikの日本全国PBFを使って索引を作成してください。地域データの場合は「いま選択した範囲」を利用できます。')
        polygons=[]
        for area in prefectures:
            outer=unary_union([Polygon(ring) for ring in area['outer']])
            if area['inner']:outer=outer.difference(unary_union([Polygon(ring) for ring in area['inner']]))
            polygons.append(outer.buffer(0))
        # Keep islands; avoid generating the broad ocean rectangle between them.
        coverage=coverage.intersection(unary_union(polygons)).buffer(0)
    # Region-limited build is a real subset of the same nationwide frame.
    if bbox:
        s,w,n,e=bbox;coverage=coverage.intersection(box(w,s,e,n))
    if coverage.is_empty:raise ValueError('指定範囲が日本データの対象範囲外です。')
    projected=transform(lambda lon,lat,z=None:forward(lat,lon,scale),coverage)
    for suffix in ('','-wal','-shm'):
        path=Path(str(root/'queue.sqlite')+suffix)
        if path.exists():path.unlink()  # incomplete planning, no project.json yet
    queue=connect(root/'queue.sqlite')
    queue.execute('CREATE TABLE tiles(tx INTEGER,tz INTEGER,state TEXT DEFAULT \'pending\',chunks INTEGER DEFAULT 0,seconds REAL,PRIMARY KEY(tx,tz))')
    total=0
    for component in getattr(projected,'geoms',[projected]):
        if component.is_empty or component.area<=0:continue
        mask=prep(component);x0,z0,x1,z1=component.bounds
        for tz in range(math.floor(z0/tile),math.ceil(z1/tile)):
            if (root/'pause.request').exists():queue.close();raise InterruptedError('区画の計画を中止しました。新規作成から再実行できます。')
            for tx in range(math.floor(x0/tile),math.ceil(x1/tile)):
                if mask.intersects(box(tx*tile,tz*tile,(tx+1)*tile,(tz+1)*tile)):
                    cursor=queue.execute('INSERT OR IGNORE INTO tiles(tx,tz) VALUES(?,?)',(tx,tz));total+=cursor.rowcount
            if tz%50==0:queue.commit();emit('planning',tiles=total)
    queue.commit();queue.close()
    scope_mode='region' if bbox else 'national'
    if scope_mode=='national':spawn=TOKYO_SPAWN
    spawn=spawn or TOKYO_SPAWN
    x,z=forward(*spawn,scale)
    if not projected.contains(__import__('shapely').geometry.Point(x,z)):
        if scope_mode=='national':raise ValueError('全国データの対象範囲に東京駅付近を確認できません。日本全国の地図データと東京都の境界を確認してください。')
        point=projected.representative_point();spawn=inverse(point.x,point.y,scale)
    west,south,east,north=coverage.bounds
    config={'format':FORMAT,'map_id':str(uuid.uuid4()),'index':str(Path(index).resolve()),'source':info,
            'scale':scale,'tile':tile,'origin':ORIGIN,'projection':'national_mercator','scope':str(Path(poly).resolve()),
            'bbox':bbox or [south,west,north,east],'spawn':spawn,'spawn_policy':'tokyo_station' if scope_mode=='national' else 'selected_area',
            'scope_mode':scope_mode,'total':total,'created':time.time(),
            'mode':'geo-only','ground_level':64,'fillground':True,'furniture_baked':True,'treasure':treasure or {'enabled':True,'probability':.25},
            'beds':beds or {'enabled':True,'probability':.25},'geology':geology if geology is not None else {'enabled':True},'villagers':villagers,'mobs':mobs,'include_navigation':include_navigation}
    atomic_json(root/'project.json',config)
    emit('planned',tiles=total,chunks=total*(tile//16)**2,project=str(root))
    return config

def load_project(root):
    return project_info(root)

def spawn_blocks(config):
    x,z=forward(*config['spawn'],config['scale'])
    return math.floor(x),math.floor(z)

def migrate_spawn(root,config):
    # Legacy projects did not distinguish full Japan from region selections.
    # A country-sized extent containing the Tokyo tile can be upgraded safely.
    s,w,n,e=config['bbox']
    national=config.get('scope_mode')=='national' or (not config.get('scope_mode') and n-s>=10 and e-w>=10)
    if national and (tuple(config.get('spawn',()))!=TOKYO_SPAWN or config.get('spawn_policy')!='tokyo_station'):
        x,z=forward(*TOKYO_SPAWN,config['scale']);tile=config['tile']
        with connect(Path(root)/'queue.sqlite') as queue:
            if not queue.execute('SELECT 1 FROM tiles WHERE tx=? AND tz=?',(math.floor(x/tile),math.floor(z/tile))).fetchone():
                raise ValueError('東京駅付近の区画が計画にありません。日本全国の範囲で新規プロジェクトを作成してください。')
        config.update(spawn=list(TOKYO_SPAWN),spawn_policy='tokyo_station',scope_mode='national')
        atomic_json(Path(root)/'project.json',config)
    return config

def configure_spawn(root,config):
    import amulet_nbt as nbt
    import leveldb
    root=Path(root);world=root/'world';x,z=spawn_blocks(config);tile=config['tile']
    with connect(root/'queue.sqlite') as queue:
        row=queue.execute('SELECT state FROM tiles WHERE tx=? AND tz=?',(x//tile,z//tile)).fetchone()
    if row is None or row[0]!='done':raise ValueError('初期スポーン地点の区画が未生成です。先に「1区画を生成」を実行してください。')
    original=(world/'level.dat').read_bytes();tag=nbt.load(original[8:],compressed=False,little_endian=True)
    marker=root/'spawn_state.json'
    previous=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
    expected={'SpawnX':x,'SpawnZ':z,'LimitedWorldOriginX':x,'LimitedWorldOriginZ':z,'LimitedWorldOriginY':config.get('ground_level',64)}
    if previous.get('x')==x and previous.get('z')==z and previous.get('y') is not None:
        expected['SpawnY']=previous['y']
        if all(int(tag.compound.get(k,nbt.IntTag(-2147483648)))==v for k,v in expected.items()):return previous
    db=leveldb.LevelDB(str(world/'db'),create_if_missing=False)
    try:data=db.get(struct.pack('<iiB',x//16,z//16,43))
    finally:db.close()
    if data is None or len(data)<512:raise ValueError('初期スポーン地点の地表高さを確認できません。該当区画のデータを確認してください。')
    # Arnis Data3D: one above the highest block in each column, X fastest.
    top=struct.unpack_from('<h',data,2*((z%16)*16+x%16))[0]
    if not 0<top<=512:raise ValueError('初期スポーン地点の高さが不正です。')
    y=top+1;expected.update(SpawnY=y)
    backups=root/'backups';backups.mkdir(exist_ok=True)
    backup=backups/('level-before-spawn-'+str(time.time_ns())+'.dat');backup.write_bytes(original)
    for key,value in expected.items():tag.compound[key]=nbt.IntTag(value)
    body=tag.save_to(compressed=False,little_endian=True);temporary=world/'level.dat.writing'
    temporary.write_bytes(original[:4]+struct.pack('<I',len(body))+body);os.replace(temporary,world/'level.dat')
    record={'x':x,'y':y,'z':z,'latitude':config['spawn'][0],'longitude':config['spawn'][1],'policy':config.get('spawn_policy','selected_area')}
    atomic_json(marker,record);emit('log',text=f'初期スポーンを設定: X{x} Y{y} Z{z}')
    return record

def status(root):
    config=load_project(root)
    with connect(Path(root)/'queue.sqlite') as db:
        completed,chunks,seconds=db.execute("SELECT COUNT(*),COALESCE(SUM(chunks),0),COALESCE(SUM(seconds),0) FROM tiles WHERE state='done'").fetchone()
    world=Path(root)/'world'
    size=sum(p.stat().st_size for p in world.rglob('*') if p.is_file()) if world.exists() else 0
    return {'total':config['total'],'completed':completed,'chunks':chunks,'seconds':seconds,'bytes':size,
            'estimated_bytes':int(size/completed*config['total']) if completed else None,
            'estimated_seconds':int(seconds/completed*config['total']) if completed else None}

def shift_nbt(data,dx,dz,level=False):
    import amulet_nbt as nbt
    cursor=0;out=bytearray()
    while cursor<len(data):
        context=nbt.ReadContext()
        tag=nbt.load(data[cursor:],compressed=False,little_endian=True,read_context=context)
        offset=context.offset
        body=tag.compound
        # Arnis writes absolute x/z and paired chest coordinates in block entities.
        for xname,zname in [('x','z'),('pairx','pairz')]+([('SpawnX','SpawnZ'),('LimitedWorldOriginX','LimitedWorldOriginZ')] if level else []):
            if xname in body:body[xname]=nbt.IntTag(int(body[xname])+dx)
            if zname in body:body[zname]=nbt.IntTag(int(body[zname])+dz)
        out.extend(tag.save_to(compressed=False,little_endian=True));cursor+=offset
        if not offset:raise ValueError('NBT読み取り位置が進みません。')
    return bytes(out)

def merge_tile(staging,world,offset,tx,tz,tile):
    import leveldb
    dx,dz=offset;cxoff,czoff=dx//16,dz//16
    target=leveldb.LevelDB(str(world/'db'),create_if_missing=True)
    source=leveldb.LevelDB(str(staging/'db'),create_if_missing=False)
    batch={};chunks=set()
    try:
        for key,value in source.iterate():
            # Only Arnis-generated overworld keys. Never copy player/global keys.
            if len(key) not in (9,10) or key[8] not in (43,44,47,49,54):
                raise ValueError('未対応の生成チャンクレコード: '+key.hex())
            cx,cz=struct.unpack('<ii',key[:8]);gx,gz=cx+cxoff,cz+czoff
            if not (tx*tile//16<=gx<(tx+1)*tile//16 and tz*tile//16<=gz<(tz+1)*tile//16):continue
            if key[8]==49:value=shift_nbt(value,dx,dz)
            batch[struct.pack('<ii',gx,gz)+key[8:]]=value;chunks.add((gx,gz))
        expected=(tile//16)**2
        if len(chunks)!=expected:raise ValueError(f'区画の欠損: {len(chunks)}/{expected} チャンク。結合は行いません。')
        target.putBatch(batch)
    finally:source.close();target.close()
    return len(chunks)

def initialise_world(staging,world,offset,config):
    world.mkdir(parents=True,exist_ok=True)
    data=(staging/'level.dat').read_bytes();body=shift_nbt(data[8:],*offset,level=True)
    import amulet_nbt as nbt
    name=config.get('world_name','MapCraft Japan（地中充填）')
    tag=nbt.load(body,compressed=False,little_endian=True);tag.compound['LevelName']=nbt.StringTag(name)
    x,z=spawn_blocks(config)
    for key,value in [('SpawnX',x),('SpawnZ',z),('LimitedWorldOriginX',x),('LimitedWorldOriginZ',z)]:tag.compound[key]=nbt.IntTag(value)
    body=tag.save_to(compressed=False,little_endian=True)
    (world/'level.dat').write_bytes(data[:4]+struct.pack('<I',len(body))+body)
    (world/'levelname.txt').write_text(name,encoding='utf-8')

def run_tiles(root,limit=0,near=None):
    from core import ENGINE_SHA256
    from tracking import MapReference
    from treasure import compile_treasure
    from beds import compile_beds
    root=Path(root);config=migrate_spawn(root,load_project(root))
    index_info(config['index'])
    if not config.get('fillground'):
        raise ValueError('この全国プロジェクトは旧版の地下空洞設定です。新しい保存先でプロジェクトを作成してください。旧データはそのまま残します。')
    if hashlib.sha256(ENGINE.read_bytes()).hexdigest()!=ENGINE_SHA256:raise ValueError('Arnis生成エンジンのハッシュが一致しません。')
    world=root/'world';tile=config['tile'];scale=config['scale']
    with connect(config['index']) as source,connect(root/'queue.sqlite') as queue:
        if dict(source.execute('SELECT key,value FROM meta'))!=config['source']:
            raise ValueError('作成時と索引データが異なります。元の索引を使用してください。')
        queue.execute("UPDATE tiles SET state='pending' WHERE state='running'");queue.commit()
        prioritized='generation_rank' in {row[1] for row in queue.execute('PRAGMA table_info(tiles)')}
        sx,sz=spawn_blocks(config);spawn_tile=(sx//tile,sz//tile)
        if (world/'level.dat').exists():
            spawn_row=queue.execute('SELECT state FROM tiles WHERE tx=? AND tz=?',spawn_tile).fetchone()
            if spawn_row and spawn_row[0]=='done':configure_spawn(root,config)
        lat,lon=near or config['spawn'];x,z=forward(lat,lon,scale);px,pz=math.floor(x/tile),math.floor(z/tile)
        count=0
        while not limit or count<limit:
            if (root/'pause.request').exists():
                emit('log',text='区画の保存が完了したため一時停止しました。');break
            row=queue.execute("SELECT tx,tz FROM tiles WHERE tx=? AND tz=? AND state!='done'",spawn_tile).fetchone()
            if row is None:
                if prioritized:row=queue.execute("SELECT tx,tz FROM tiles WHERE state='pending' ORDER BY generation_rank,generation_distance,tz,tx LIMIT 1").fetchone()
                else:row=queue.execute("SELECT tx,tz FROM tiles WHERE state!='done' ORDER BY (tx-?)*(tx-?)+(tz-?)*(tz-?),tz,tx LIMIT 1",(px,px,pz,pz)).fetchone()
            if row is None:break
            if shutil.disk_usage(root).free<4*1024**3:raise OSError('空き容量が4GB未満のため停止しました。空きを確保して再開してください。')
            tx,tz=row;started=time.monotonic();spec=tile_spec(tx,tz,scale,tile)
            work=root/'staging';work.mkdir(exist_ok=True)
            queue.execute("UPDATE tiles SET state='running' WHERE tx=? AND tz=?",row);queue.commit()
            payload=extract(source,spec['bbox']);atomic_json(work/'osm.json',payload)
            generated=work/'generated'
            # Clear ONLY the fixed private staging output of this project.
            if not generated.resolve().is_relative_to(root.resolve()):raise ValueError('生成用の作業フォルダーがプロジェクト外を参照しています。')
            if generated.exists():shutil.rmtree(generated)
            generated.mkdir()
            args=[str(ENGINE),'--bedrock','--bbox='+','.join(f'{v:.12f}' for v in spec['bbox']),
                '--projection','web_mercator','--scale',f"{spec['engine_scale']:.15f}",
                '--output-dir',str(generated.resolve()),'--file',str((work/'osm.json').resolve()),
                '--mode','geo-only','--ground-level','64','--overture','false','--canopy-height','false',
                '--map-item','false','--gamemode','creative','--vanilla-height','--no-3d','--fillground']
            emit('tile',tx=tx,tz=tz,completed=status(root)['completed'],total=config['total'])
            with (work/'engine.log').open('wb') as log:
                # stdout stays bounded; full per-tile diagnostics remain in staging.
                result=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=str(BASE),timeout=1800)
            if result.returncode:raise RuntimeError(f'区画 {tx},{tz} の生成に失敗しました。staging/engine.log を確認してください。')
            archives=list(generated.glob('*.mcworld'))
            if len(archives)!=1:raise RuntimeError('生成ワールドを特定できません。')
            unpack=work/'unpacked'
            if not unpack.resolve().is_relative_to(root.resolve()):raise ValueError('展開先がプロジェクト外を参照しています。')
            if unpack.exists():shutil.rmtree(unpack)
            with zipfile.ZipFile(archives[0]) as archive:archive.extractall(unpack)
            if config.get('geology',{}).get('enabled'):
                from geology_backend import apply_directory
                ref=MapReference(config['map_id'],tuple(config['bbox']),scale,1,1,projection='national_mercator',origin=ORIGIN)
                geology_cache=Path(os.environ.get('MAPCRAFT_GEOLOGY_CACHE',BASE/'cache/geology'))
                geology_record=apply_directory(unpack,ref,geology_cache,lambda text:emit('log',text=text),offset=spec['offset'],backend=config['geology'].get('backend'))
                atomic_json(root/'geology-last-tile.json',geology_record)
            # A stable identity keeps loot selection/dynamic properties stable on resume.
            ref=MapReference(config['map_id'],tuple(config['bbox']),scale,1,1,projection='national_mercator',origin=ORIGIN,
                             treasure_enabled=config['treasure']['enabled'],treasure_probability=config['treasure']['probability'],
                             beds_enabled=config.get('beds',{}).get('enabled',True),beds_probability=config.get('beds',{}).get('probability',.25))
            plans=compile_treasure(ref,payload)
            plans['plans']=[p for p in plans['plans'] if tx*tile<=p['points'][0][0]<(tx+1)*tile and tz*tile<=p['points'][0][1]<(tz+1)*tile]
            beds=compile_beds(ref,payload)
            beds['plans']=[p for p in beds['plans'] if tx*tile<=p['points'][0][0]<(tx+1)*tile and tz*tile<=p['points'][0][1]<(tz+1)*tile]
            beds['selected']=len(beds['plans'])
            from furniture_backend import bake_directory
            # Only this tile's chunks are merged. Keep both bed halves inside it.
            furniture=bake_directory(unpack,plans['plans'],beds['plans'],offset=spec['offset'],
                                     owned_bounds=(tx*tile,tz*tile,(tx+1)*tile,(tz+1)*tile),notify=lambda text:emit('log',text=text))
            if not (world/'level.dat').exists():initialise_world(unpack,world,spec['offset'],config)
            chunks=merge_tile(unpack,world,spec['offset'],tx,tz,tile)
            assets=root/'tile_data';assets.mkdir(exist_ok=True)
            atomic_json(assets/f'{tx}_{tz}.json',{'bbox':spec['bbox'],'treasure':plans,'beds':beds,'furniture':furniture})
            # DB batch is atomic. A crash before this commit safely repeats the same
            # private build tile. Installed/played worlds are never edited here.
            queue.execute("UPDATE tiles SET state='done',chunks=?,seconds=? WHERE tx=? AND tz=?",(chunks,time.monotonic()-started,tx,tz));queue.commit()
            if row==spawn_tile:configure_spawn(root,config)
            count+=1;emit('progress',**status(root))
    return status(root)

def export_world(root,destination):
    root=Path(root);config=migrate_spawn(root,load_project(root))
    index_info(config['index'])
    if not config.get('fillground'):raise ValueError('旧版の地下空洞設定のプロジェクトです。新しいプロジェクトで地中を含めて再生成してください。')
    if not status(root)['completed']:raise ValueError('まず1区画以上を生成してください。')
    configure_spawn(root,config)
    from furniture_backend import ensure_project_furniture
    ensure_project_furniture(root,lambda text:emit('log',text=text));config=load_project(root)
    from villagers_backend import ensure_project_villagers
    ensure_project_villagers(root,lambda text:emit('log',text=text))
    # Export a snapshot, leaving the persistent private database extensible.
    from national_pack import install_pack
    if config.get('include_navigation',True):install_pack(root,config)
    else:
        from tracking import MapReference
        reference=MapReference(config['map_id'],tuple(config['bbox']),config['scale'],1,1,projection=config['projection'],origin=tuple(config['origin']),furniture_baked=True)
        atomic_json(root/'world/mapcraft_tracking.json',asdict(reference))
    from ecology_backend import ensure_project_ecology
    ensure_project_ecology(root,lambda text:emit('log',text=text))
    destination=Path(destination);temporary=destination.with_suffix('.exporting')
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
            for path in (root/'world').rglob('*'):
                if path.is_file() and path.name!='LOCK':archive.write(path,path.relative_to(root/'world').as_posix())
            archive.writestr('mapcraft_national.json',json.dumps({'project':config,'progress':status(root)},ensure_ascii=False))
        os.replace(temporary,destination)
    finally:
        if temporary.exists():temporary.unlink()
    emit('exported',path=str(destination),**status(root))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['index','create','run','status','export']);parser.add_argument('--request',required=True)
    args=parser.parse_args();data=json.loads(Path(args.request).read_text(encoding='utf-8'))
    if args.action=='index':
        # Also serialize indexes shared by different project windows.
        with project_lock(Path(data['index']).resolve().parent):index_pbf(data['source'],data['index'],Path(data['project'])/'pause.request')
        return
    with project_lock(data['project']):
        if args.action=='create':create_project(data['project'],data['index'],data['scale'],data['poly'],data.get('bbox'),data.get('spawn'),data.get('treasure'),beds=data.get('beds'),geology=data.get('geology'),villagers=data.get('villagers'),mobs=data.get('mobs'),include_navigation=data.get('include_navigation',False))
        elif args.action=='run':run_tiles(data['project'],data.get('limit',0),data.get('near'))
        elif args.action=='status':emit('status',**status(data['project']))
        elif args.action=='export':export_world(data['project'],data['destination'])

if __name__=='__main__':
    try:main()
    except Exception as error:
        if not isinstance(error,(ValueError,InterruptedError)):
            import traceback
            traceback.print_exc(file=sys.stderr)
        emit('error',text=str(error));sys.exit(1)
