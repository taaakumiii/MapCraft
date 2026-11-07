"""Build one native Bedrock add-on containing both the behavior and HUD resource packs."""
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import shutil
import uuid
import zipfile

from version import PACK_VERSION as VERSION
ASSETS=Path(__file__).resolve().parent/'native_minimap'
RESOURCE_UUID=str(uuid.uuid5(uuid.NAMESPACE_URL,'MapCraft/native-minimap/resource'))

def pack_version(reference,map_root):
    # Re-importing an identical UUID/version is ignored by Minecraft. Advance
    # the patch revision when the creator changes the treasure options.
    path=map_root/'pack_revision.json'
    settings=[reference.treasure_enabled,reference.treasure_probability,reference.beds_enabled,reference.beds_probability,reference.furniture_baked]
    previous=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    revision=previous.get('revision',0) if previous.get('base')==VERSION else 0
    if previous.get('base')==VERSION and previous.get('settings')!=settings:revision+=1
    version=[VERSION[0],VERSION[1],VERSION[2]+revision]
    path.write_text(json.dumps({'base':VERSION,'settings':settings,'revision':revision}),encoding='utf-8')
    return version

def pack_files(reference=None):
    if reference is None:raise ValueError('現実の地図には、対象ワールドの generation.json が必要です。')
    if reference.projection=='national_mercator':raise ValueError('全国ワールドの実地図は全国プロジェクトに同梱されています。全国モードの「生成済みの範囲を .mcworld に出力」を使用してください。')
    from realmap import prepare_map, hud_document, frame_bytes
    map_root,atlas=prepare_map(reference)
    from municipalities import prepare_boundaries
    boundaries=prepare_boundaries(reference,map_root)
    from treasure import prepare_treasure
    treasure=prepare_treasure(reference,map_root)
    from beds import prepare_beds
    beds=prepare_beds(reference,map_root)
    if reference.furniture_baked:
        treasure={**treasure,'plans':[]};beds={**beds,'plans':[]}
    version=pack_version(reference,map_root)
    identity=uuid.UUID(reference.map_id) if reference else uuid.uuid5(uuid.NAMESPACE_URL,'MapCraft/native-minimap/generic')
    # Same behavior header as 0.3: importing the update replaces the large GPS action bar.
    header=str(uuid.uuid5(identity,'behavior-header'))
    script=str(uuid.uuid5(identity,'script-module'))
    suffix=' '+reference.map_id[:8] if reference else ''
    resource_id=str(uuid.uuid5(identity,'real-map-resources'))
    setting=f'宝箱 {reference.treasure_probability*100:g}%' if reference.treasure_enabled else '宝箱 オフ'
    bed_setting=f'ベッド {reference.beds_probability*100:g}%' if reference.beds_enabled else 'ベッド オフ'
    bp={'format_version':2,'header':{'name':'MapCraft Real Map'+suffix,'description':'実地図・市町村への到着通知・'+setting+'・'+bed_setting,
        'uuid':header,'version':version,'min_engine_version':[1,26,0]},
        'modules':[{'type':'script','language':'javascript','uuid':script,'version':version,'entry':'scripts/main.js'}],
        'dependencies':[{'module_name':'@minecraft/server','version':'2.6.0'},
                        {'module_name':'@minecraft/server-ui','version':'2.1.0'},
                        {'uuid':resource_id,'version':version}],
        'metadata':{'authors':['MapCraft contributors','Timothy Cope (upstream minimap)'],'license':'MIT'}}
    rp={'format_version':2,'header':{'name':'MapCraft Real Map HUD'+suffix,'description':'OpenStreetMapの現実の道路・地名を同梱した地図です。',
        'uuid':resource_id,'version':version,'min_engine_version':[1,26,0]},
        'modules':[{'type':'resources','uuid':str(uuid.uuid5(uuid.UUID(resource_id),'module')),'version':version}],
        'metadata':{'authors':['MapCraft contributors','Timothy Cope (upstream minimap)'],'license':'MIT'}}
    files={}
    for folder,prefix in [('behavior','MapCraft_Behavior'),('resources','MapCraft_Resources')]:
        for path in (ASSETS/folder).rglob('*'):
            if path.is_file() and path.name!='manifest.json':
                relative=path.relative_to(ASSETS/folder).as_posix();files[prefix+'/'+relative]=path.read_bytes()
    geo='MapCraft_Behavior/scripts/geographic.js'
    payload=json.dumps(asdict(reference),ensure_ascii=True,separators=(',',':')) if reference else 'null'
    files[geo]=files[geo].replace(b'/*MAPCRAFT_CONFIG*/null',payload.encode('utf-8'))
    main=(ASSETS.parent/'realmap.js').read_text(encoding='utf-8').replace('/*MAPCRAFT_ATLAS*/null',json.dumps(atlas,separators=(',',':')))
    files['MapCraft_Behavior/scripts/main.js']=main.encode('utf-8')
    files['MapCraft_Behavior/scripts/arrival.js']=(ASSETS.parent/'arrival.js').read_bytes()
    files['MapCraft_Behavior/scripts/treasure.js']=(ASSETS.parent/'treasure.js').read_bytes()
    from loot import script as loot_script
    files['MapCraft_Behavior/scripts/loot.js']=(ASSETS.parent/'loot.js').read_bytes()
    files['MapCraft_Behavior/scripts/loot-catalogue.js']=loot_script().encode('utf-8')
    files['MapCraft_Behavior/scripts/beds.js']=(ASSETS.parent/'beds.js').read_bytes()
    files['MapCraft_Behavior/scripts/bed-plans.js']=('export const bedPlans='+json.dumps(beds['plans'],separators=(',',':'))+';\n').encode('utf-8')
    files['MapCraft_Behavior/map_data/bed-plan.json']=(map_root/'bed-plan.json').read_bytes()
    files['MapCraft_Behavior/scripts/treasure-plans.js']=('export const treasurePlans='+json.dumps(treasure['plans'],separators=(',',':'))+';\n').encode('utf-8')
    files['MapCraft_Behavior/map_data/treasure-plan.json']=(map_root/'treasure-plan.json').read_bytes()
    files['MapCraft_Behavior/scripts/municipalities.js']=('export const municipalities='+json.dumps(boundaries['municipalities'],ensure_ascii=False,separators=(',',':'))+';\n').encode('utf-8')
    for prefix in ('MapCraft_Behavior','MapCraft_Resources'):
        files[prefix+'/map_data/boundaries-osm.json']=(map_root/'boundaries-osm.json').read_bytes()
        files[prefix+'/map_data/municipalities.json']=(map_root/'municipalities.json').read_bytes()
    files['MapCraft_Resources/ui/hud_screen.json']=json.dumps(hud_document(),separators=(',',':')).encode('utf-8')
    for path in (map_root/'cells').glob('*.png'):
        files['MapCraft_Resources/textures/ui/mapcraft_real/'+path.name]=path.read_bytes()
    files['MapCraft_Resources/textures/ui/mapcraft_real/frame.png']=frame_bytes()
    for prefix in ('MapCraft_Behavior','MapCraft_Resources'):
        files[prefix+'/map_data/OSM-CREDITS.txt']=(map_root/'CREDITS.txt').read_bytes()
    files['MapCraft_Resources/map_data/osm.json']=(map_root/'osm.json').read_bytes()
    for prefix,manifest in [('MapCraft_Behavior',bp),('MapCraft_Resources',rp)]:
        files[prefix+'/manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
        for notice in ('LICENSE-upstream.txt','CREDITS-upstream.md','UPSTREAM.json'):
            files[prefix+'/'+notice]=(ASSETS/notice).read_bytes()
        files[prefix+'/LICENSE-MapCraft.txt']=(ASSETS.parent/'licenses/MapCraft-LICENSE.txt').read_bytes()
        files[prefix+'/THIRD-PARTY-NOTICES.md']=(ASSETS.parent/'THIRD_PARTY_NOTICES.md').read_bytes()
    if reference:
        metadata=json.dumps(asdict(reference)).encode('utf-8')
        files['mapcraft_tracking.json']=metadata
        files['MapCraft_Behavior/mapcraft_tracking.json']=metadata
    return header,resource_id,files

def write_addon(reference,destination,files_bundle=None):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    _,_,files=files_bundle if files_bundle is not None else pack_files(reference)
    temporary=destination.with_suffix('.addon.tmp')
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,allowZip64=False) as archive:
            # Minecraft imports a .mcaddon as a bundle of complete .mcpack archives.
            for prefix in ('MapCraft_Behavior','MapCraft_Resources'):
                memory=io.BytesIO()
                with zipfile.ZipFile(memory,'w',zipfile.ZIP_DEFLATED,allowZip64=False) as pack:
                    for name,contents in files.items():
                        if name.startswith(prefix+'/'):pack.writestr(name[len(prefix)+1:],contents)
                archive.writestr(prefix+'.mcpack',memory.getvalue())
        os.replace(temporary,destination)
    finally:
        if temporary.exists():temporary.unlink()
    return destination

def read_addon_bundle(path):
    files={}
    with zipfile.ZipFile(path) as archive:
        for prefix in ('MapCraft_Behavior','MapCraft_Resources'):
            with zipfile.ZipFile(io.BytesIO(archive.read(prefix+'.mcpack'))) as pack:
                for name in pack.namelist():
                    if not name.endswith('/'):files[prefix+'/'+name]=pack.read(name)
    bp=json.loads(files['MapCraft_Behavior/manifest.json']);rp=json.loads(files['MapCraft_Resources/manifest.json'])
    files['mapcraft_tracking.json']=files['MapCraft_Behavior/mapcraft_tracking.json']
    return bp['header']['uuid'],rp['header']['uuid'],files

def embed_native_pack(world_file,reference,files_bundle=None):
    """Only for a newly generated world: add both packs, retaining all existing world bytes."""
    world_file=Path(world_file);temporary=world_file.with_suffix('.native.tmp')
    header,resource,files=files_bundle if files_bundle is not None else pack_files(reference)
    version=json.loads(files['MapCraft_Behavior/manifest.json'])['header']['version']
    prefixes={'MapCraft_Behavior/':'behavior_packs/mapcraft_minimap/',
              'MapCraft_Resources/':'resource_packs/mapcraft_minimap/'}
    try:
        with zipfile.ZipFile(world_file) as original,zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as out:
            lists={}
            for filename,pack_id in [('world_behavior_packs.json',header),('world_resource_packs.json',resource)]:
                entries=json.loads(original.read(filename).decode('utf-8-sig')) if filename in original.namelist() else []
                entries=[item for item in entries if item.get('pack_id')!=pack_id]
                entries.append({'pack_id':pack_id,'version':version});lists[filename]=entries
            for item in original.infolist():
                if item.filename in (*lists,'mapcraft_tracking.json') or item.filename.startswith(('behavior_packs/mapcraft_gps/',*prefixes.values())):continue
                with original.open(item) as src,out.open(item,'w') as dst:shutil.copyfileobj(src,dst,1024*1024)
            for name,contents in files.items():
                for before,after in prefixes.items():
                    if name.startswith(before):name=after+name[len(before):];break
                out.writestr(name,contents)
            for filename,entries in lists.items():out.writestr(filename,json.dumps(entries))
        os.replace(temporary,world_file)
    finally:
        if temporary.exists():temporary.unlink()
