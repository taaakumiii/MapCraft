"""Explicit distribution allowlists. No creator data or local configuration is collected."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from version import VERSION
APP_FILES=['actor_batch.py','bathymetry.py','bathymetry_backend.py','parallel_furniture.py','parallel_plans.py','ecology.py','ecology_backend.py','villagers.py','villagers_backend.py','volcanoes.py','active-volcanoes.json','municipal-population.json','gpu_driver.py','geology_gpu.ptx','geology_gpu.cu','scripts/finish_phase.py','scripts/finalize_furniture.py','furniture_backend.py','loot_native.py','app.py','arrival.js','autogen.py','beds.js','beds.py','core.py','geocoder.py','geology.py',
 'geology_backend.py','gps.js','jobcache.py','jobs.py','loot.js','loot.py','minimap.py','municipalities.py',
 'national_backend.py','national_pack.py','national_navigation.py','national_state.py','national_ui.py','nativepack.py','realmap.js',
 'realmap.py','map_water.py','regions-loader.js','survival-items.json','tracking.py','treasure.js','treasure.py','version.py','scripts/continue_region.py','ocean_backend.py','bathymetry_bulk.py']

def paths(folder):
    return sorted(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')

def package(executable,engine,runtime,destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    archive=destination/f'MapCraft-Creator-{VERSION}-windows-x64.zip';manifest={}
    def add(output,path,name):
        data=path.read_bytes();output.writestr('MapCraft/'+name,data);manifest[name]=hashlib.sha256(data).hexdigest()
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        add(output,Path(executable),'MapCraft.exe');add(output,Path(engine),'engine/arnis-windows.exe')
        for name in APP_FILES:add(output,ROOT/name,name)
        for folder in ('native_minimap','licenses','docs','config'):
            for path in paths(ROOT/folder):
                if path.name=='autogen.json':continue
                add(output,path,path.relative_to(ROOT).as_posix())
        for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md','CHANGELOG.md','はじめに.txt'):
            add(output,ROOT/name,name)
        add(output,ROOT/'national_data/japan.poly','national_data/japan.poly')
        for path in paths(Path(runtime)):add(output,path,'engine/national-runtime/'+path.relative_to(runtime).as_posix())
        output.writestr('MapCraft/FILE-MANIFEST.json',json.dumps({'version':VERSION,'sha256':manifest},indent=2))
    print(archive)
    return archive

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--exe',type=Path,default=ROOT/'dist/MapCraft.exe')
    parser.add_argument('--engine',type=Path,default=ROOT/'engine/arnis-windows.exe')
    parser.add_argument('--runtime',type=Path,default=ROOT/'engine/national-runtime');parser.add_argument('--out',type=Path,default=ROOT/'release')
    args=parser.parse_args();package(args.exe,args.engine,args.runtime,args.out)
