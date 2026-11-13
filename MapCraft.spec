from pathlib import Path
from importlib.metadata import distribution
root = Path(SPECPATH).resolve()
assets = [(str(root / 'native_minimap'), 'native_minimap'),(str(root/'licenses'),'licenses'),
          (str(root/'THIRD_PARTY_NOTICES.md'),'.')]
for name in ('gps.js','realmap.js','arrival.js','treasure.js','beds.js','loot.js','regions-loader.js','survival-items.json','active-volcanoes.json','municipal-population.json'):
    assets.append((str(root / name), '.'))
for package in ('numpy','shapely'):
    dependency=distribution(package)
    for item in dependency.files or ():
        if '.dist-info/licenses/' in item.as_posix():
            relative=Path(item.as_posix().split('.dist-info/licenses/',1)[1])
            assets.append((str(dependency.locate_file(item)),str(Path('licenses')/('GUI-'+package)/relative.parent)))
a = Analysis([str(root/'app.py')], pathex=[str(root)], binaries=[], datas=assets,
             hiddenimports=[], hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,a.binaries,a.datas,[],name='MapCraft',debug=False,
          bootloader_ignore_signals=False,strip=False,upx=False,console=False)
