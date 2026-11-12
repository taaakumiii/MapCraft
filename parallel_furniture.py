"""Parallel placement against an immutable snapshot, followed by one bulk writer.

Workers never open the live LevelDB. Key preconditions and the existing placement
journal bridge calculation, sorted bulk writes and tile checkpoint publication.
Only run under the creator's project/control locks, never on a played world.
"""
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing,nullcontext
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import time

BASE=Path(__file__).resolve().parent;sys.path.insert(0,str(BASE))
import leveldb
import national_backend as n
from furniture_backend import VERSION,bake_directory

def digest(value):return hashlib.sha256(value).digest() if value is not None else None

def database_fingerprint(path):
    rows=[]
    for file in sorted(Path(path).iterdir()):
        if file.is_file() and file.suffix in ('.ldb','.sst','.log'):
            stat=file.stat()
            # Opening a database rotates manifests and creates an empty WAL.
            # Neither changes records; nonempty WALs and SSTs do affect them.
            if stat.st_size:rows.append((file.name,stat.st_size,stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

def save_shard(path,identity,result):
    path=Path(path);path.parent.mkdir(exist_ok=True);temporary=path.with_suffix('.'+str(os.getpid())+'.tmp')
    if temporary.exists():temporary.unlink()
    with closing(sqlite3.connect(temporary)) as con:
        con.execute('CREATE TABLE metadata(k TEXT PRIMARY KEY,v TEXT)')
        con.execute('CREATE TABLE changes(k BLOB PRIMARY KEY,v BLOB) WITHOUT ROWID')
        con.execute('CREATE TABLE reads(k BLOB PRIMARY KEY,h BLOB) WITHOUT ROWID')
        metadata={k:v for k,v in result.items() if k not in ('changes','before')}
        con.executemany('INSERT INTO metadata VALUES(?,?)',[('identity',identity),('result',json.dumps(metadata,ensure_ascii=False))])
        con.executemany('INSERT INTO changes VALUES(?,?)',result['changes'].items())
        con.executemany('INSERT INTO reads VALUES(?,?)',result['before'].items());con.commit()
    os.replace(temporary,path)

def load_shard(path,identity):
    if not Path(path).exists():return None
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as con:
            if con.execute("SELECT v FROM metadata WHERE k='identity'").fetchone()!=(identity,):return None
            if con.execute('PRAGMA quick_check').fetchone()!=('ok',):return None
            result=json.loads(con.execute("SELECT v FROM metadata WHERE k='result'").fetchone()[0])
            result.update(changes=dict(con.execute('SELECT k,v FROM changes')),before=dict(con.execute('SELECT k,h FROM reads')))
            result['cache_hit']=True;return result
    except (sqlite3.DatabaseError,ValueError,TypeError):return None

def record_hash(hasher,key,value):
    hasher.update(struct.pack('<IQ',len(key),len(value)));hasher.update(key);hasher.update(value)

def rebuilt_database(db,overlay,target,notify,batch_bytes,*,patches_sorted=None,expected_chunks=None):
    target=Path(target);output=leveldb.LevelDB(str(target),create_if_missing=True)
    expected=hashlib.sha256();patches=iter(sorted(overlay.items()) if patches_sorted is None else patches_sorted);patch=next(patches,None);batch={};size=count=chunks=0
    def append(key,value):
        nonlocal size,count,batch,chunks
        record_hash(expected,key,value);batch[key]=value;size+=len(key)+len(value);count+=1
        chunks+=int(len(key)==9 and key[8]==43)
        if size>=batch_bytes:
            output.putBatch(batch);batch={};size=0
            notify(f'ワールドDBを順序保存: {count:,} レコード')
    try:
        for key,value in db.iterate():
            while patch is not None and patch[0]<key:
                append(*patch);patch=next(patches,None)
            if patch is not None and patch[0]==key:append(*patch);patch=next(patches,None)
            else:append(key,value)
        while patch is not None:append(*patch);patch=next(patches,None)
        if batch:output.putBatch(batch)
    finally:output.close()
    if expected_chunks is not None and chunks!=expected_chunks:raise ValueError(f'新しいDBのチャンク数が不一致です: {chunks:,}/{expected_chunks:,}。元のDBは保持しています。')
    check=leveldb.LevelDB(str(target),create_if_missing=False);actual=hashlib.sha256();checked=0
    try:
        for key,value in check.iterate():
            record_hash(actual,key,value);checked+=1
            if checked%1000000==0:notify(f'新DBの読み戻し検証: {checked:,}/{count:,} レコード')
    finally:check.close()
    if actual.digest()!=expected.digest() or count!=checked:raise ValueError('新しいDBの全レコード検証に失敗しました。元のDBを保持します。')
    return {'records':count,'sha256':actual.hexdigest(),'chunks':chunks}

def recover_swap(root):
    root=Path(root).resolve();marker=root/'parallel-db-swap.json'
    if not marker.exists():return
    record=json.loads(marker.read_text(encoding='utf-8'));source=Path(record['source']).resolve();backup=Path(record['backup']).resolve()
    if source!=root/'world/db' or not backup.is_relative_to(root/'backups'):raise ValueError('DB引継ぎのパスが不正です。')
    if not source.exists() and backup.exists():os.replace(backup,source)

def swap_database(root,fresh,verification):
    root=Path(root).resolve();source=(root/'world/db').resolve();fresh=Path(fresh).resolve();backup=root/'backups'/('before-parallel-'+str(time.time_ns()))
    backup=backup.resolve()
    if not source.is_relative_to(root/'world') or not fresh.is_relative_to(root) or not backup.is_relative_to(root/'backups'):
        raise ValueError('DB引継ぎのパスがプロジェクト外です。')
    backup.parent.mkdir(exist_ok=True)
    n.atomic_json(root/'parallel-db-swap.json',{'source':str(source),'backup':str(backup),'fresh':str(fresh),'verified':verification})
    os.replace(source,backup)
    try:os.replace(fresh,source)
    except BaseException:
        if not source.exists():os.replace(backup,source)
        raise

class MemoryDB:
    def __init__(self,records):self.records=records;self.changes={};self.reads=set()
    def get(self,key):
        self.reads.add(key)
        if key in self.changes:return self.changes[key]
        return self.records[key]
    def putBatch(self,values):self.changes.update(values)

def make_snapshot(db,path,notify):
    count=0;batch=[]
    con=sqlite3.connect(path)
    try:
        con.execute('PRAGMA journal_mode=OFF');con.execute('PRAGMA synchronous=OFF')
        con.execute('CREATE TABLE records(cx INTEGER,cz INTEGER,k BLOB PRIMARY KEY,v BLOB) WITHOUT ROWID')
        for key,value in db.iterate():
            if len(key) not in (9,10) or key[8] not in (43,47,49):continue
            cx,cz=struct.unpack('<ii',key[:8])
            # Placement only needs the first 512 heightmap bytes, not biomes.
            if key[8]==43:value=value[:512]
            batch.append((cx,cz,key,value));count+=1
            if len(batch)>=8192:
                con.executemany('INSERT INTO records VALUES(?,?,?,?)',batch);batch=[]
            if count%500000==0:notify(f'並列計算用スナップショット: {count:,} レコード')
        if batch:con.executemany('INSERT INTO records VALUES(?,?,?,?)',batch)
        con.execute('CREATE INDEX coordinates ON records(cx,cz)');con.commit()
    finally:con.close()
    return count

def rows_for(snapshot,bounds):
    x0,z0,x1,z1=bounds
    with closing(sqlite3.connect(Path(snapshot).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as con:
        result={}
        # Both dimensions are ranges. A single cx range scans every cz for
        # those columns; equality on cx lets SQLite bound the second index key.
        for cx in range(math.floor((x0-80)/16),math.ceil((x1+80)/16)):
            result.update(con.execute('SELECT k,v FROM records WHERE cx=? AND cz>=? AND cz<?',
                (cx,math.floor((z0-80)/16),math.ceil((z1+80)/16))))
        return result

def work_tile(task,overrides=None):
    root=Path(task['root']);path=root/'tile_data'/task['name'];data=json.loads(path.read_text(encoding='utf-8'));config=task['config']
    identity=hashlib.sha256(json.dumps([2,task.get('source_identity'),config,data],sort_keys=True).encode()).hexdigest()
    shard=Path(task['shards'])/(task['name']+'.sqlite') if task.get('shards') else None
    if shard is not None and overrides is None:
        cached=load_shard(shard,identity)
        if cached is not None:return cached
        for previous_source in task.get('source_aliases',[]):
            alias=hashlib.sha256(json.dumps([2,previous_source,config,data],sort_keys=True).encode()).hexdigest()
            cached=load_shard(shard,alias)
            if cached is None:continue
            original=rows_for(task['snapshot'],cached['bounds'])
            # Physical SST filenames change during compaction. Reuse an old
            # shard only after every recorded read/write precondition matches
            # the current immutable snapshot, including absent keys.
            if all(digest(original.get(key))==value for key,value in cached['before'].items()):
                save_shard(shard,identity,cached);return cached
    from parallel_plans import prepare_tile
    prepare_tile(task)
    prepared=json.loads((root/'planning-cache'/task['name']).read_text(encoding='utf-8'))
    data.update(treasure=prepared['treasure'],beds=prepared['beds'])
    if 'ecology' in prepared:data['ecology']=prepared['ecology']
    points=[p for kind in ('treasure','beds') for plan in data[kind]['plans'] for p in plan['points']]
    x0,z0,x1,z1=task['bounds']
    if points:x0=min(x0,min(p[0] for p in points));z0=min(z0,min(p[1] for p in points));x1=max(x1,max(p[0] for p in points)+1);z1=max(z1,max(p[1] for p in points)+1)
    records=rows_for(task['snapshot'],(x0,z0,x1,z1))
    if overrides:records.update(overrides)
    database=MemoryDB(records)
    with tempfile.TemporaryDirectory(prefix='placement-',dir=task['scratch']) as folder:
        folder=Path(folder);state=folder/'journal.sqlite'
        with closing(sqlite3.connect(Path(task['journal']).resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(state)) as dest:source.backup(dest)
        previous=data.get('furniture',{});beds_done=previous.get('version',0)>=1
        report=bake_directory(folder,data['treasure']['plans'],[] if beds_done else data['beds']['plans'],state_path=state,database=database,notify=lambda _:None)
        if beds_done:report['counts']['beds']=previous.get('counts',{}).get('beds',0)
        data['furniture']=report
        with closing(sqlite3.connect(state)) as con:
            con.execute('ATTACH DATABASE ? AS baseline',(str(Path(task['journal']).resolve()),))
            changed=con.execute('SELECT p.kind,p.id,p.status,p.data FROM plans p LEFT JOIN baseline.plans b ON p.kind=b.kind AND p.id=b.id WHERE b.id IS NULL OR p.status!=b.status OR p.data!=b.data').fetchall()
    result={'name':task['name'],'data':data,'journal':changed,'changes':database.changes,'bounds':[x0,z0,x1,z1],
            'before':{key:digest(records.get(key)) for key in database.reads|database.changes.keys()}}
    if shard is not None and overrides is None:save_shard(shard,identity,result)
    return result

def ensure_parallel_furniture(root,notify=print,workers=8,bulk_bytes=64*1024**2,rebuild=False,work_dir=None,snapshot_hint=None,source_aliases=()):
    if not 1<=workers<=32:raise ValueError('家具の並列数は1〜32です。')
    root=Path(root).resolve();config=n.load_project(root);started=time.monotonic()
    if work_dir is not None:
        work_dir=Path(work_dir).resolve()
        if not work_dir.is_relative_to(root) or not work_dir.name.startswith('.parallel-furniture-'):raise ValueError('作業フォルダーが不正です。')
        work_dir.mkdir(exist_ok=True)
    recover_swap(root)
    (root/'planning-cache').mkdir(exist_ok=True)
    with closing(n.connect(root/'queue.sqlite')) as queue:tiles=queue.execute("SELECT tx,tz FROM tiles WHERE state='done' ORDER BY tz,tx").fetchall()
    all_data={};needed=[]
    for tx,tz in tiles:
        name=f'{tx}_{tz}.json';data=json.loads((root/'tile_data'/name).read_text(encoding='utf-8'));all_data[name]=data.get('furniture',{})
        if data.get('furniture',{}).get('version')!=VERSION:needed.append((tx,tz))
    if not needed:
        from furniture_backend import ensure_project_furniture
        return ensure_project_furniture(root,notify)
    state_path=root/'furniture-state.sqlite';journal=sqlite3.connect(state_path,timeout=30)
    journal.execute('PRAGMA journal_mode=WAL')
    journal.execute('CREATE TABLE IF NOT EXISTS plans(kind TEXT,id TEXT,status TEXT,data TEXT,PRIMARY KEY(kind,id))')
    journal.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT)')
    schema=journal.execute("SELECT value FROM meta WHERE key='furniture_schema'").fetchone()
    if schema is None or int(schema[0])<VERSION:journal.execute("DELETE FROM plans WHERE kind='chest' AND status IN ('skipped','blocked')")
    journal.execute("INSERT OR REPLACE INTO meta VALUES('furniture_schema',?)",(str(VERSION),));journal.commit()
    db=None;compute_seconds=0;write_seconds=0;conflicts=0;done=0;accepted=0;cache_hits=0;overlay={};pending={};pending_bytes=0;waiting=[];staged=[]
    try:
        db=leveldb.LevelDB(str(root/'world/db'),create_if_missing=False)
        with (nullcontext(str(work_dir)) if work_dir else tempfile.TemporaryDirectory(prefix='.parallel-furniture-',dir=root)) as scratch:
            snapshot=Path(scratch)/'source.sqlite'
            notify(f'家具の並列仕上げ: 残り{len(needed):,}区画 / CPU最大{workers}プロセス / 保存は1プロセス')
            source_identity=database_fingerprint(root/'world/db')
            if snapshot_hint and source_identity==snapshot_hint.get('source_identity') and Path(snapshot_hint['snapshot']).resolve()==snapshot.resolve() and snapshot.is_file():
                notify('検証済みの並列計算用データを再利用（地形の再生成なし）')
            else:
                snapshot.unlink(missing_ok=True);make_snapshot(db,snapshot,notify)
            n.atomic_json(Path(scratch)/'session.json',{'source_identity':source_identity,'snapshot':str(snapshot)})
            tasks=[]
            for tx,tz in needed:
                size=config['tile'];tasks.append({'root':str(root),'name':f'{tx}_{tz}.json','config':config,'snapshot':str(snapshot),
                    'scratch':scratch,'journal':str(state_path),'bounds':(tx*size,tz*size,(tx+1)*size,(tz+1)*size),
                    'shards':str(Path(scratch)/'shards'),'source_identity':source_identity,'source_aliases':list(source_aliases)})
            def flush():
                nonlocal pending,pending_bytes,waiting,write_seconds,done
                if not waiting:return
                # Publish intents before a single atomic native batch.
                for result in waiting:
                    for kind,identity,status,data in result['journal']:
                        intent='placing' if status in ('placed','recovered') else status
                        journal.execute('INSERT OR REPLACE INTO plans VALUES(?,?,?,?)',(kind,identity,intent,data))
                journal.commit();when=time.monotonic()
                if rebuild:
                    for result in waiting:
                        path=Path(scratch)/(result['name']+'.staged')
                        n.atomic_json(path,{'data':result['data'],'journal':result['journal']});staged.append((result['name'],path))
                    notify(f'家具の計算完了: {len(staged):,}/{len(tasks):,} 区画（後でDBを一括保存）')
                    pending={};pending_bytes=0;waiting=[];return
                if pending:db.putBatch(dict(sorted(pending.items())))
                write_seconds+=time.monotonic()-when
                for result in waiting:
                    journal.executemany('INSERT OR REPLACE INTO plans VALUES(?,?,?,?)',result['journal'])
                journal.commit()
                for result in waiting:
                    n.atomic_json(root/'tile_data'/result['name'],result['data']);all_data[result['name']]=result['data']['furniture'];done+=1
                notify(f'家具の仕上げ保存: {done:,}/{len(tasks):,} 区画（CPU{workers}並列、再計算{conflicts}件）')
                pending={};pending_bytes=0;waiting=[]
            with ProcessPoolExecutor(max_workers=workers) as pool:
                source=iter(tasks);futures=deque()
                for _ in range(workers*2):
                    task=next(source,None)
                    if task is not None:futures.append((task,pool.submit(work_tile,task)))
                while futures:
                    task,future=futures.popleft();when=time.monotonic();result=future.result();compute_seconds+=time.monotonic()-when
                    following=next(source,None)
                    if following is not None:futures.append((following,pool.submit(work_tile,following)))
                    cache_hits+=int(result.get('cache_hit',False))
                    if any(digest(overlay[key])!=expected for key,expected in result['before'].items() if key in overlay):
                        conflicts+=1;x0,z0,x1,z1=result['bounds']
                        overrides={key:value for key,value in overlay.items() if len(key) in (9,10) and
                            math.floor((x0-80)/16)<=struct.unpack_from('<i',key)[0]<math.ceil((x1+80)/16) and
                            math.floor((z0-80)/16)<=struct.unpack_from('<i',key,4)[0]<math.ceil((z1+80)/16)}
                        result=pool.submit(work_tile,task,overrides).result()
                        if any(digest(overlay[key])!=expected for key,expected in result['before'].items() if key in overlay):raise ValueError('並列配置の競合を解決できませんでした。保存済み状態を保持して停止します。')
                    for key,value in result['changes'].items():
                        pending_bytes+=len(value)-len(pending.get(key,b''));pending[key]=value;overlay[key]=value
                    waiting.append(result)
                    accepted+=1
                    notify(f'家具を地区別DBで計算: {accepted:,}/{len(tasks):,} 区画（CPU{workers}並列、境界調整{conflicts}件、再利用{cache_hits}件）')
                    # Commit in bulk rather than rewriting a global DB for every chest.
                    if pending_bytes>=bulk_bytes or len(waiting)>=32:flush()
            flush()
            if rebuild:
                when=time.monotonic();fresh=Path(scratch)/'rebuilt-db'
                verification=rebuilt_database(db,overlay,fresh,notify,bulk_bytes)
                db.close();db=None;swap_database(root,fresh,verification);write_seconds+=time.monotonic()-when
                for name,path in staged:
                    value=json.loads(path.read_text(encoding='utf-8'))
                    journal.executemany('INSERT OR REPLACE INTO plans VALUES(?,?,?,?)',value['journal']);journal.commit()
                    n.atomic_json(root/'tile_data'/name,value['data']);all_data[name]=value['data']['furniture']
        totals={'chests':0,'beds':0,'tiles':len(tiles)}
        for data in all_data.values():
            for kind in ('chests','beds'):totals[kind]+=data['counts'][kind]
        signature=hashlib.sha256(json.dumps([config['map_id'],config['treasure'],config.get('beds')],sort_keys=True).encode()).hexdigest()
        summary={'version':VERSION,'mode':'native_blocks','treasure_unit':'floor','totals':totals,'signature':signature,
            'performance':{'workers':workers,'bulk_bytes':bulk_bytes,'rebuild':rebuild,'conflicts':conflicts,'cache_hits':cache_hits,'seconds':round(time.monotonic()-started,3),'waiting_seconds':round(compute_seconds,3),'write_seconds':round(write_seconds,3)}}
        n.atomic_json(root/'world/mapcraft_furniture.json',summary);config['furniture_baked']=True;config['furniture_schema']=VERSION;n.atomic_json(root/'project.json',config)
        return summary
    finally:
        if db is not None:db.close()
        journal.close()
