"""Prepare a per-world player ZIP from a fresh MapCraft generator export.

Creator paths are removed. This does not remove player data from the LevelDB:
only use fresh generator outputs, never worlds exported after playing them.
"""
import argparse
import io
import json
from pathlib import Path
import sys
import zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from version import VERSION,PACK_VERSION

def player_package(source,destination,source_data=None,name="MapCraft-World",demo=False):
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*",name) or ".." in name:raise ValueError("Use a simple package name without directories.")
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    world=destination/(name+'.mcworld');addon=destination/(name+'.mcaddon')
    files={};tracking=None
    with zipfile.ZipFile(source) as original:
        if 'mapcraft_tracking.json' not in original.namelist():raise ValueError('A MapCraft generator export is required.')
        tracking=json.loads(original.read('mapcraft_tracking.json'))
        for item in original.infolist():
            if item.filename.startswith(('behavior_packs/','resource_packs/')) and not item.is_dir():files[item.filename]=original.read(item)
        bp=[name for name in files if name.startswith('behavior_packs/') and name.endswith('/manifest.json')]
        rp=[name for name in files if name.startswith('resource_packs/') and name.endswith('/manifest.json')]
        if len(bp)!=1 or len(rp)!=1:raise ValueError('This package helper expects a single MapCraft behavior/resource pair.')
        bp_root=bp[0].removesuffix('manifest.json');rp_root=rp[0].removesuffix('manifest.json')
        for manifest_path,label in [(bp[0],name),(rp[0],name+' HUD')]:
            manifest=json.loads(files[manifest_path]);manifest['header']['name']=label;manifest['header']['version']=PACK_VERSION
            for module in manifest['modules']:module['version']=PACK_VERSION
            for dependency in manifest.get('dependencies',[]):
                if 'uuid' in dependency:dependency['version']=PACK_VERSION
            files[manifest_path]=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
        for prefix in (bp_root,rp_root):
            files[prefix+'LICENSE-MapCraft.txt']=(ROOT/'LICENSE').read_bytes()
            files[prefix+'THIRD-PARTY-NOTICES.md']=(ROOT/'THIRD_PARTY_NOTICES.md').read_bytes()
            files[prefix+'mapcraft_tracking.json']=json.dumps(tracking).encode()
            if source_data:files[prefix+'map_data/SOURCE-OSM.json']=Path(source_data).read_bytes()
            files[prefix+'map_data/SOURCE.txt']=b'Map data: OpenStreetMap contributors, ODbL 1.0.\nExtract provider: Geofabrik\nhttps://download.geofabrik.de/asia/japan.html\nhttps://www.openstreetmap.org/copyright\n'
        with zipfile.ZipFile(world,'w',zipfile.ZIP_DEFLATED) as output:
            for item in original.infolist():
                name=item.filename
                if item.is_dir() or name.startswith(('behavior_packs/','resource_packs/')):continue
                if name in ('mapcraft_national.json','world_icon.jpeg'):continue
                if name in ('world_behavior_packs.json','world_resource_packs.json'):
                    entries=json.loads(original.read(item))
                    for entry in entries:entry['version']=PACK_VERSION
                    output.writestr(name,json.dumps(entries));continue
                with original.open(item) as src,output.open(item,'w') as dst:
                    while block:=src.read(1024*1024):dst.write(block)
            for name,data in files.items():output.writestr(name,data)
            output.writestr('MAPCRAFT-DISTRIBUTION.json',json.dumps({'version':VERSION,'type':'Tokyo Station area, one initial generation tile' if demo else 'Per-world distribution',
                'scale':tracking['scale'],'map_id':tracking['map_id'],'data':'OpenStreetMap / Geofabrik','status':'preview; not a completed nationwide world' if demo else 'generator export'},ensure_ascii=False))
        with zipfile.ZipFile(addon,'w',zipfile.ZIP_DEFLATED) as bundle:
            for root,name in [(bp_root,'MapCraft_Behavior.mcpack'),(rp_root,'MapCraft_Resources.mcpack')]:
                memory=io.BytesIO()
                with zipfile.ZipFile(memory,'w',zipfile.ZIP_DEFLATED) as pack:
                    for path,data in files.items():
                        if path.startswith(root):pack.writestr(path[len(root):],data)
                bundle.writestr(name,memory.getvalue())
    return world,addon

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--world',required=True,type=Path);parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--source-data',type=Path);parser.add_argument('--name',default='MapCraft-World');parser.add_argument('--demo',action='store_true');args=parser.parse_args()
    for result in player_package(args.world,args.out,args.source_data,args.name,args.demo):print(result)
