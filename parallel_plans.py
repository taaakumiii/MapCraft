"""Read-only parallel planning while the existing world writer keeps running."""
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from contextlib import closing
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
BASE=Path(__file__).resolve().parent;sys.path.insert(0,str(BASE))
import national_backend as n
from tracking import MapReference

@lru_cache(maxsize=1)
def population_boundaries(index):
    with closing(n.connect(index)) as db:
        return [n.unpacked(row[0]) for row in db.execute('SELECT data FROM towns')],[n.unpacked(row[0]) for row in db.execute('SELECT data FROM prefectures')]

def plan_key(config,data):
    index=Path(config.get('index',''))
    source=(str(index),index.stat().st_size,index.stat().st_mtime_ns) if index.is_file() else None
    return hashlib.sha256(json.dumps([1,config['map_id'],config['scale'],config['projection'],config['origin'],data['bbox'],config['treasure'],config.get('beds'),source],sort_keys=True).encode()).hexdigest()

def prepare_tile(task):
    root=Path(task['root']);data=json.loads((root/'tile_data'/task['name']).read_text(encoding='utf-8'));config=task['config']
    cache=root/'planning-cache'/task['name'];key=plan_key(config,data)
    wants_mobs=config.get('mobs',{}).get('wildlife_enabled') or config.get('mobs',{}).get('bosses_enabled')
    wants_residents=config.get('villagers',{}).get('enabled',False)
    if cache.exists():
        prior=json.loads(cache.read_text(encoding='utf-8'))
        if prior.get('key')==key and (not wants_mobs or prior.get('ecology',{}).get('options')==config['mobs']) and (not wants_residents or prior.get('villagers',{}).get('options')==config['villagers']):return {'name':task['name'],'cached':True}
    ref=MapReference(config['map_id'],tuple(config['bbox']),config['scale'],1,1,projection=config['projection'],origin=tuple(config['origin']),
        treasure_enabled=config['treasure']['enabled'],treasure_probability=config['treasure']['probability'],
        beds_enabled=config.get('beds',{}).get('enabled',True),beds_probability=config.get('beds',{}).get('probability',.25))
    payload=None
    if data['treasure'].get('version',1)<3 or data.get('beds') is None or wants_mobs or wants_residents:
        with closing(sqlite3.connect(Path(config['index']).resolve().as_uri()+'?mode=ro',uri=True)) as source:payload=n.extract(source,data['bbox'])
    x0,z0,x1,z1=task['bounds'];treasure=data['treasure']
    if treasure.get('version',1)<3:
        from treasure import compile_treasure
        treasure=compile_treasure(ref,payload)
        treasure['plans']=[p for p in treasure['plans'] if x0<=p['points'][0][0]<x1 and z0<=p['points'][0][1]<z1];treasure['selected']=len(treasure['plans'])
    beds=data.get('beds')
    if beds is None:
        from beds import compile_beds
        beds=compile_beds(ref,payload)
        beds['plans']=[p for p in beds['plans'] if x0<=p['points'][0][0]<x1 and z0<=p['points'][0][1]<z1];beds['selected']=len(beds['plans'])
    prepared={'key':key,'treasure':treasure,'beds':beds}
    if wants_mobs:
        from ecology import compile_mobs
        prepared['ecology']=compile_mobs(ref,payload,config['mobs'],owned_bounds=task['bounds'])
    if wants_residents:
        from villagers import compile_villagers
        towns,prefs=population_boundaries(config['index'])
        prepared['villagers']=compile_villagers(ref,payload,towns,config['villagers'],prefectures=prefs,owned_bounds=task['bounds'])
    if cache.exists() and 'ecology' not in prepared:
        other=json.loads(cache.read_text(encoding='utf-8'))
        if other.get('key')==key and 'ecology' in other:prepared['ecology']=other['ecology']
    temporary=cache.with_suffix('.'+str(os.getpid())+'.tmp')
    temporary.write_text(json.dumps(prepared,ensure_ascii=False,separators=(',',':')),encoding='utf-8');os.replace(temporary,cache)
    return {'name':task['name'],'cached':False}

def prepare_all(root,notify=print,workers=12,mobs=None,villagers=None):
    root=Path(root);config=n.load_project(root)
    if mobs is not None:config={**config,'mobs':mobs}
    if villagers is not None:config={**config,'villagers':villagers}
    with closing(n.connect(root/'queue.sqlite')) as db:tiles=db.execute("SELECT tx,tz FROM tiles WHERE state='done' ORDER BY tz,tx").fetchall()
    (root/'planning-cache').mkdir(exist_ok=True);tasks=[];size=config['tile']
    for tx,tz in tiles:tasks.append({'root':str(root),'name':f'{tx}_{tz}.json','config':config,'bounds':(tx*size,tz*size,(tx+1)*size,(tz+1)*size)})
    marker=root/'parallel-plans-progress.json';state={'status':'running','workers':workers,'pid':os.getpid(),'completed':0,'total':len(tasks),'cached':0,'started':time.time()}
    n.atomic_json(marker,state)
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            source=iter(tasks);futures=set()
            for _ in range(workers*2):
                task=next(source,None)
                if task:futures.add(pool.submit(prepare_tile,task))
            while futures:
                done,futures=wait(futures,return_when=FIRST_COMPLETED)
                for future in done:
                    result=future.result();state['completed']+=1;state['cached']+=int(result['cached']);state['updated']=time.time();n.atomic_json(marker,state)
                    notify(f'配置計画をCPU{workers}並列で準備: {state["completed"]:,}/{len(tasks):,} 区画')
                    task=next(source,None)
                    if task:futures.add(pool.submit(prepare_tile,task))
        state.update(status='complete',updated=time.time());n.atomic_json(marker,state);return state
    except Exception as error:
        state.update(status='failed',error=str(error),updated=time.time());n.atomic_json(marker,state);raise

def cached(root,config,data,name):
    path=Path(root)/'planning-cache'/name
    if path.exists():
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('key')==plan_key(config,data):return value
    return None
