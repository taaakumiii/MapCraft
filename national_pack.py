"""Incremental sparse offline real-map HUD assets for a connected Japan world."""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import uuid

def cell_code(value):
    if not 0<=value<2**32:raise ValueError('地図セルIDの上限です。')
    return ''.join(chr(97+((value>>shift)&15)) for shift in range(28,-1,-4))

def install_pack(root,config):
    from national_backend import connect,extract,unpacked,atomic_json,emit
    from tracking import MapReference
    from realmap import prepare_map,hud_document,CELL,code,frame_bytes
    from nativepack import ASSETS,VERSION
    from beds import compile_beds
    from treasure import compile_treasure
    from PIL import Image
    root=Path(root);world=root/'world';bp=world/'behavior_packs/mapcraft_national';rp=world/'resource_packs/mapcraft_national'
    for folder,target in [('behavior',bp),('resources',rp)]:
        shutil.copytree(ASSETS/folder,target,dirs_exist_ok=True)
    textures=rp/'textures/ui/mapcraft_real';textures.mkdir(parents=True,exist_ok=True)
    atlasdb=connect(root/'atlas.sqlite')
    atlasdb.executescript('CREATE TABLE IF NOT EXISTS cells(id INTEGER PRIMARY KEY,zoom INTEGER,x INTEGER,y INTEGER,UNIQUE(zoom,x,y));'
                         'CREATE TABLE IF NOT EXISTS rendered(tx INTEGER,tz INTEGER,PRIMARY KEY(tx,tz));')
    ref=MapReference(config['map_id'],tuple(config['bbox']),config['scale'],1,1,projection='national_mercator',origin=tuple(config['origin']),
        treasure_enabled=config['treasure']['enabled'],treasure_probability=config['treasure']['probability'],
        beds_enabled=config.get('beds',{}).get('enabled',True),beds_probability=config.get('beds',{}).get('probability',.25))
    scripts=bp/'scripts';scripts.mkdir(exist_ok=True)
    regions=scripts/'regions';regions.mkdir(exist_ok=True);region_keys=[]
    with connect(config['index']) as source,connect(root/'queue.sqlite') as queue:
        for tx,tz in queue.execute("SELECT tx,tz FROM tiles WHERE state='done' ORDER BY tz,tx"):
            tile_path=root/'tile_data'/f'{tx}_{tz}.json'
            tile=json.loads(tile_path.read_text(encoding='utf-8'))
            bbox=tile['bbox'];s,w,n,e=bbox
            if tile['treasure'].get('version',1)<2:
                data=compile_treasure(ref,extract(source,bbox));size=config['tile']
                data['plans']=[p for p in data['plans'] if tx*size<=p['points'][0][0]<(tx+1)*size and tz*size<=p['points'][0][1]<(tz+1)*size]
                data['selected']=len(data['plans']);tile['treasure']=data;atomic_json(tile_path,tile)
            # Older filled projects acquire bed plans on export as well, so
            # already-generated tiles are included without rebuilding their DB.
            beds=tile.get('beds')
            if beds is None or beds.get('enabled')!=ref.beds_enabled or beds.get('probability')!=ref.beds_probability:
                beds=compile_beds(ref,extract(source,bbox));size=config['tile']
                beds['plans']=[p for p in beds['plans'] if tx*size<=p['points'][0][0]<(tx+1)*size and tz*size<=p['points'][0][1]<(tz+1)*size]
                beds['selected']=len(beds['plans']);tile['beds']=beds;atomic_json(tile_path,tile)
            key=f'{tx}_{tz}';region_keys.append(key)
            (regions/f't{key}.js').write_text('export const treasure='+json.dumps(tile['treasure']['plans'],separators=(',',':'))+';\nexport const beds='+json.dumps(beds['plans'],separators=(',',':'))+';\n',encoding='utf-8')
            if atlasdb.execute('SELECT 1 FROM rendered WHERE tx=? AND tz=?',(tx,tz)).fetchone():continue
            # Render neighbouring vectors too; every cell includes whole roads
            # and labels across the generation tile boundary, without tile APIs.
            payload=extract(source,(s-.015,w-.02,n+.015,e+.02))
            scratch=root/'staging/nav';scratch.mkdir(parents=True,exist_ok=True)
            for name in ('atlas.json',):
                if (scratch/name).exists():(scratch/name).unlink()
            atomic_json(scratch/'osm.json',payload)
            small=MapReference(ref.map_id,tuple(bbox),ref.scale,1,1)
            _,atlas=prepare_map(small,root=scratch,zooms=[12,14])
            for level in atlas['levels']:
                for row in range(level['rows']):
                    for col in range(level['cols']):
                        x,y=level['gx']+col,level['gy']+row
                        atlasdb.execute('INSERT OR IGNORE INTO cells(zoom,x,y) VALUES(?,?,?)',(level['zoom'],x,y))
                        ident=atlasdb.execute('SELECT id FROM cells WHERE zoom=? AND x=? AND y=?',(level['zoom'],x,y)).fetchone()[0]
                        shutil.copyfile(scratch/'cells'/('c'+code(level['offset']+row*level['cols']+col)+'.png'),textures/('c'+cell_code(ident)+'.png'))
            atlasdb.execute('INSERT INTO rendered VALUES(?,?)',(tx,tz));atlasdb.commit()
            emit('log',text=f'実地図を追加: {tx}, {tz}')
        towns=[unpacked(row[0]) for row in source.execute('SELECT data FROM towns ORDER BY id')]
    levels=[]
    for zoom in (12,14):
        cells={f'{x},{y}':cell_code(ident) for ident,x,y in atlasdb.execute('SELECT id,x,y FROM cells WHERE zoom=?',(zoom,))}
        levels.append({'zoom':zoom,'cells':cells})
    atlasdb.close()
    Image.new('RGB',(CELL,CELL),'#d8dce0').save(textures/'czzzzzzzz.png')
    (textures/'frame.png').write_bytes(frame_bytes())
    scripts=bp/'scripts';scripts.mkdir(exist_ok=True)
    base=Path(__file__).resolve().parent
    geo=(ASSETS/'behavior/scripts/geographic.js').read_text(encoding='utf-8').replace('/*MAPCRAFT_CONFIG*/null',json.dumps(asdict(ref),separators=(',',':')))
    (scripts/'geographic.js').write_text(geo,encoding='utf-8')
    (scripts/'main.js').write_text("import './regions-loader.js';\n"+(base/'realmap.js').read_text(encoding='utf-8').replace('/*MAPCRAFT_ATLAS*/null',json.dumps({'sparse':True,'levels':levels},separators=(',',':'))),encoding='utf-8')
    for name in ('arrival.js','treasure.js','beds.js','loot.js','regions-loader.js'):shutil.copyfile(base/name,scripts/name)
    from loot import script as loot_script
    (scripts/'loot-catalogue.js').write_text(loot_script(),encoding='utf-8')
    (scripts/'bed-plans.js').write_text('export const bedPlans=[];',encoding='utf-8')
    (scripts/'region-index.js').write_text('export const tileSize='+str(config['tile'])+';export const regionTiles='+json.dumps(region_keys,separators=(',',':'))+';',encoding='utf-8')
    (scripts/'municipalities.js').write_text('export const municipalities='+json.dumps(towns,ensure_ascii=False,separators=(',',':'))+';',encoding='utf-8')
    (scripts/'treasure-plans.js').write_text('export const treasurePlans=[];',encoding='utf-8')
    (rp/'ui/hud_screen.json').write_text(json.dumps(hud_document(digits=8),separators=(',',':')),encoding='utf-8')
    identity=uuid.UUID(ref.map_id);header=str(uuid.uuid5(identity,'behavior-header'));resource=str(uuid.uuid5(identity,'real-map-resources'))
    revision_path=root/'gameplay_pack_revision.json'
    revision=json.loads(revision_path.read_text(encoding='utf-8')).get('revision',0)+1 if revision_path.exists() else 0
    atomic_json(revision_path,{'revision':revision})
    version=[VERSION[0],VERSION[1],revision]
    for target,kind,ident,module in [(bp,'script',header,'script-module'),(rp,'resources',resource,'resource-module')]:
        manifest={'format_version':2,'header':{'name':'MapCraft Japan '+('Real Map' if kind=='script' else 'HUD'),'description':'日本共通座標・実地図・市町村到着通知・地域の宝箱・ベッド','uuid':ident,'version':version,'min_engine_version':[1,26,0]},
            'modules':[{'type':kind,'uuid':str(uuid.uuid5(identity,module)),'version':version}]}
        if kind=='script':
            manifest['modules'][0].update(language='javascript',entry='scripts/main.js')
            manifest['dependencies']=[{'module_name':'@minecraft/server','version':'2.6.0'},{'module_name':'@minecraft/server-ui','version':'2.1.0'},{'uuid':resource,'version':version}]
        atomic_json(target/'manifest.json',manifest)
        for name in ('LICENSE-upstream.txt','CREDITS-upstream.md','UPSTREAM.json'):shutil.copyfile(ASSETS/name,target/name)
        shutil.copyfile(base/'licenses/MapCraft-LICENSE.txt',target/'LICENSE-MapCraft.txt')
        shutil.copyfile(base/'THIRD_PARTY_NOTICES.md',target/'THIRD-PARTY-NOTICES.md')
        (target/'OSM-CREDITS.txt').write_text('Map data © OpenStreetMap contributors, ODbL 1.0.\nhttps://www.openstreetmap.org/copyright\nData extract: Geofabrik\nhttps://download.geofabrik.de/asia/japan.html\nCartography rendered by MapCraft from local OSM vectors.\n',encoding='utf-8')
    for filename,ident in [('world_behavior_packs.json',header),('world_resource_packs.json',resource)]:atomic_json(world/filename,[{'pack_id':ident,'version':version}])
    atomic_json(world/'mapcraft_tracking.json',asdict(ref))
    if config.get('geology',{}).get('enabled'):
        from geology import CREDITS
        (world/'GEOLOGY-CREDITS.txt').write_text(CREDITS,encoding='utf-8')
