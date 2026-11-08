"""Use Bedrock's flat generator for synthetic offshore water in unmapped chunks.

Only private creator level.dat metadata is changed. Existing chunks, actors,
inventories and coordinates are untouched; no runtime pack is required.
"""
import hashlib
import json
from pathlib import Path
import struct

import amulet_nbt as nbt


def options(value=None):
    value=value or {}
    enabled=value.get('enabled',False)
    depth=value.get('depth',60)
    if type(enabled) is not bool or type(depth) is not int or not 4<=depth<=120:
        raise ValueError('簡易海の設定が不正です。深さは4〜120ブロックで指定してください。')
    return {'enabled':enabled,'depth':depth}


def flat_layers(sea_level,depth):
    if type(sea_level) is not int or not 4<=sea_level<=255:
        raise ValueError('簡易海の水面高さが不正です。')
    floor=sea_level-depth
    if floor<-61:raise ValueError('海底が生成可能な高さを下回っています。')
    # Post-1.18 flat layers begin at Y=-64. Keep a completely solid seabed.
    layers=[('bedrock',1),('stone',floor+62),('gravel',1),('sand',1),('water',depth)]
    return {'biome_id':0,'block_layers':[{'block_name':'minecraft:'+name,'count':count} for name,count in layers],
            'encoding_version':6,'structure_options':None,'world_version':'version.post_1_18'}


def apply_directory(world,settings=None,*,sea_level=64,backups=None,notify=print):
    from national_backend import atomic_json
    settings=options(settings)
    if not settings['enabled']:return None
    world=Path(world);level=world/'level.dat';original=level.read_bytes()
    tag=nbt.load(original[8:],compressed=False,little_endian=True)
    if int(tag.compound.get('Generator',nbt.IntTag(-1)))!=2:
        raise ValueError('簡易海は作成用のフラットワールドだけに適用できます。')
    expected=flat_layers(sea_level,settings['depth'])
    before=json.loads(str(tag.compound['FlatWorldLayers']))
    marker=world/'mapcraft_ocean.json'
    previous=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
    if before!=expected and before.get('preset_id')!='TheVoid' and previous.get('flat_layers')!=before:
        raise ValueError('未知の地形生成設定を保持しました。簡易海への変更を中止します。')
    if before!=expected:
        folder=Path(backups or world.parent/'backups');folder.mkdir(parents=True,exist_ok=True)
        backup=folder/('level-before-ocean-'+hashlib.sha256(original).hexdigest()[:16]+'.dat')
        if not backup.exists():backup.write_bytes(original)
        tag.compound['FlatWorldLayers']=nbt.StringTag(json.dumps(expected,separators=(',',':')))
        body=tag.save_to(compressed=False,little_endian=True)
        import os,uuid
        temporary=level.with_name('level.dat.'+uuid.uuid4().hex+'.ocean-writing')
        try:
            temporary.write_bytes(original[:4]+struct.pack('<I',len(body))+body)
            os.replace(temporary,level)
        finally:temporary.unlink(missing_ok=True)
    report={'version':1,'mode':'native_flat_ocean','options':settings,'sea_level':sea_level,
            'floor_y':sea_level-settings['depth'],'bottom_y':-64,'flat_layers':expected,
            'runtime_pack':False,'generated_on_demand':True,'existing_chunks_preserved':True,
            'scope':'All ungenerated overworld chunks use synthetic flat ocean, including outside the selected real-map area.',
            'bathymetry':'Synthetic offshore water has a constant depth, not surveyed bathymetry. Generated coast chunks retain their real-data correction.'}
    atomic_json(marker,report)
    (world/'OCEAN-README.txt').write_text('未生成部分はMinecraft標準のフラット生成により、水面と海底を持つ簡易海になります。追加MOD・パックは不要です。\n'
        '外洋は一定水深のゲーム用補完で、実際の海底地形ではありません。生成済みの島・沿岸・建物は維持します。\n'
        '地図の対象外にある未生成の陸地の位置も簡易海になります。実地図として再現するには作成用プロジェクトで範囲を拡張してください。\n'
        '探索先の変更やゲームの保存処理に応じてプレイ用ワールドの容量が増えます。\n',encoding='utf-8')
    notify(f'未生成海域を簡易海で補完: 水面Y{sea_level} / 水深{settings["depth"]} / 追加パック不要')
    return report


def ensure_project_ocean(root,notify=print):
    from national_backend import load_project
    root=Path(root);config=load_project(root)
    return apply_directory(root/'world',config.get('ocean'),sea_level=config.get('ground_level',64),backups=root/'backups',notify=notify)
