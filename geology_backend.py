"""Conservative stone-only geological decoration of private Arnis Bedrock output."""
from dataclasses import asdict
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from itertools import islice,chain
import atexit
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time
import zipfile
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
import amulet_nbt as nbt
import leveldb
import numpy as np
from geology import Survey,VERSION,CREDITS
from tracking import MapReference

def decode(data):
    if data[0]!=9:raise ValueError('未対応のサブチャンク形式です。')
    offset=3;layers=[]
    for _ in range(data[1]):
        header=data[offset];bits=header>>1;offset+=1
        if header&1 or bits not in (0,1,2,3,4,5,6,8,16):raise ValueError('未対応のブロック格納形式です。')
        if bits:
            per=32//bits;count=(4096+per-1)//per
            words=np.frombuffer(data,dtype='<u4',count=count,offset=offset);offset+=4*count
            indices=((words[:,None]>>np.arange(per,dtype=np.uint32)*bits)&((1<<bits)-1)).ravel()[:4096].copy()
        else:indices=np.zeros(4096,dtype=np.uint32)
        size=struct.unpack_from('<I',data,offset)[0];offset+=4;palette=[]
        if not 1<=size<=65536:raise ValueError('パレット長が不正です。')
        for _ in range(size):
            context=nbt.ReadContext();tag=nbt.load(data[offset:],compressed=False,little_endian=True,read_context=context)
            if not context.offset:raise ValueError('パレットを読み取れません。')
            palette.append(tag);offset+=context.offset
        if int(indices.max())>=size:raise ValueError('ブロックパレットの参照が不正です。')
        layers.append((indices,palette))
    if offset!=len(data):raise ValueError('サブチャンクの末尾を解釈できません。')
    return layers

def encode(header,layers):
    output=bytearray(header)
    for indices,palette in layers:
        bits=next(b for b in (1,2,3,4,5,6,8,16) if len(palette)<=2**b);per=32//bits
        padded=np.zeros(((4096+per-1)//per)*per,dtype=np.uint32);padded[:4096]=indices
        words=np.bitwise_or.reduce(padded.reshape(-1,per)<<np.arange(per,dtype=np.uint32)*bits,axis=1)
        output.append(bits<<1);output.extend(words.astype('<u4').tobytes());output.extend(struct.pack('<I',len(palette)))
        for tag in palette:output.extend(tag.save_to(compressed=False,little_endian=True))
    return bytes(output)

HOSTS={'granite':['granite','diorite','stone'],'basalt':['basalt','andesite','stone'],
       'volcanic':['andesite','tuff','stone'],'limestone':['calcite','stone','stone'],
       'sandstone':['sandstone','sandstone','stone'],'mudstone':['deepslate','clay','stone'],
       'metamorphic':['deepslate','tuff','stone'],'sedimentary':['stone','andesite','stone'],
       'sediment':['sandstone','gravel','clay'],'fill':['sand','gravel','clay'],'drained':['clay','dirt','sand']}
LOCAL=np.arange(4096,dtype=np.uint32)
LX=LOCAL//256;LZ=LOCAL//16%16;LY=LOCAL%16
MATERIALS=('stone','granite','diorite','basalt','andesite','tuff','calcite','sandstone','deepslate','clay','gravel','sand','dirt','iron_ore','gold_ore')
MATERIAL_IDS={name:i for i,name in enumerate(MATERIALS)}
_GPU=None
_AUTO_BACKENDS={}

def material_descriptor(row):
    _,_,cx,cz,section,rock,offset=row[:7]
    hosts=[MATERIAL_IDS[name] for name in HOSTS.get(rock,HOSTS['sedimentary'])] if rock!='unknown' else [-1,0,0]
    iron=160 if rock in ('basalt','metamorphic') else 100 if rock in ('volcanic','granite') else 50
    gold=12 if rock in ('volcanic','granite') else 3
    return [cx*16+offset[0],cz*16+offset[1],section*16,*hosts,iron,gold,int(rock in ('sediment','fill','drained'))]

def apply_materials(data,section,materials):
    layers=decode(data);indices,palette=layers[0]
    stone=[i for i,v in enumerate(palette) if str(v.compound['name'])=='minecraft:stone' and not v.compound['states']]
    if not stone:return data,0
    y=LY.astype(np.int64)+section*16
    changed=np.isin(indices,stone)&(y>=1)&(y<=56)&(materials!=0)
    if not changed.any():return data,0
    version=palette[0].compound['version'];slots={str(v.compound['name']).removeprefix('minecraft:'):i for i,v in enumerate(palette) if not v.compound['states']}
    for code in sorted(np.unique(materials[changed]),key=lambda c:MATERIALS[int(c)]):
        name=MATERIALS[int(code)]
        if name not in slots:
            states={'pillar_axis':nbt.StringTag('y')} if name in ('basalt','deepslate') else {}
            slots[name]=len(palette);palette.append(nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag('minecraft:'+name),'states':nbt.CompoundTag(states),'version':version})))
        indices[changed&(materials==code)]=slots[name]
    return encode(data[:3],layers),int(changed.sum())

def material_batch(payload):
    rows,materials=payload
    return [(row[0],*finish_volcano(row,*apply_materials(row[1],row[4],materials[i]))) for i,row in enumerate(rows)]

def gpu_batch(rows,gpu,codec_pool=None):
    materials=gpu.materials([material_descriptor(row) for row in rows])
    if codec_pool is None:return material_batch((rows,materials))
    chunks=[(rows[i:i+128],materials[i:i+128]) for i in range(0,len(rows),128)]
    return [item for batch in codec_pool.map(material_batch,chunks) for item in batch]

def gpu_session():
    global _GPU
    if _GPU is None:
        from gpu_driver import MaterialsGPU
        _GPU=MaterialsGPU();atexit.register(_GPU.close)
    return _GPU

def noise(x,y,z):
    with np.errstate(over='ignore'):
        value=np.asarray(x,dtype=np.uint32)*np.uint32(73856093)^np.asarray(y,dtype=np.uint32)*np.uint32(19349663)^np.asarray(z,dtype=np.uint32)*np.uint32(83492791)
        value^=value>>13;value*=np.uint32(1274126177);value^=value>>16
    return value

def decorate(data,cx,cz,section,rock,offset=(0,0)):
    if rock=='unknown':return data,0
    layers=decode(data);indices,palette=layers[0]
    stone=[i for i,v in enumerate(palette) if str(v.compound['name'])=='minecraft:stone' and not v.compound['states']]
    if not stone:return data,0
    y=LY.astype(np.int64)+section*16
    mask=np.isin(indices,stone)&(y>=1)&(y<=56)
    if not mask.any():return data,0
    x=LX.astype(np.int64)+cx*16+offset[0];z=LZ.astype(np.int64)+cz*16+offset[1]
    strata=noise(np.floor_divide(x,8),np.floor_divide(y,5),np.floor_divide(z,8))
    hosts=HOSTS.get(rock,HOSTS['sedimentary']);names=np.full(4096,'stone',dtype='U20')
    for i,name in enumerate(hosts):names[mask&(strata%4==i)]=name
    # Loose deposits only model the upper part; no claim about deep geology.
    if rock in ('sediment','fill','drained'):names[y<44]='stone'
    cluster=noise(np.floor_divide(x,3),np.floor_divide(y,3),np.floor_divide(z,3))%10000
    sparse=noise(x,y,z)%4==0
    iron=160 if rock in ('basalt','metamorphic') else 100 if rock in ('volcanic','granite') else 50
    gold=12 if rock in ('volcanic','granite') else 3
    names[mask&sparse&(cluster<iron)]='iron_ore'
    names[mask&sparse&(cluster>=iron)&(cluster<iron+gold)&(y<40)]='gold_ore'
    changed=mask&(names!='stone')
    if not changed.any():return data,0
    version=palette[0].compound['version'];slots={str(v.compound['name']).removeprefix('minecraft:'):i for i,v in enumerate(palette) if not v.compound['states']}
    for name in np.unique(names[changed]):
        if name not in slots:
            states={'pillar_axis':nbt.StringTag('y')} if name in ('basalt','deepslate') else {}
            slots[name]=len(palette);palette.append(nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag('minecraft:'+name),'states':nbt.CompoundTag(states),'version':version})))
        indices[changed&(names==name)]=slots[name]
    return encode(data[:3],layers),int(changed.sum())

def finish_volcano(row,result,count):
    if len(row)<8 or row[7] is None:return result,count
    _,original,cx,cz,section,_,offset,volcano=row
    old=decode(original);old_indices,old_palette=old[0]
    stone=[i for i,v in enumerate(old_palette) if str(v.compound['name'])=='minecraft:stone' and not v.compound['states']]
    eligible=np.isin(old_indices,stone)
    for indices,palette in old[1:]:
        air=[i for i,v in enumerate(palette) if str(v.compound['name'])=='minecraft:air']
        eligible&=np.isin(indices,air)
    y=LY.astype(np.int64)+section*16
    eligible&=(y>=3)&(y<=50)
    if not eligible.any():return result,count
    x=LX.astype(np.int64)+cx*16+offset[0];z=LZ.astype(np.int64)+cz*16+offset[1]
    names=np.full(4096,'',dtype='U20')
    cluster=noise(x//3,y//3,z//3)%10000;sparse=noise(x,y,z)%3==0
    strength=volcano['strength']/1000
    # Ordinary minerals are enriched; diamonds/netherite retain their usual gates.
    low=0
    for name,frequency in [('iron_ore',260),('copper_ore',320),('gold_ore',35),('redstone_ore',55),('lapis_ore',35)]:
        high=low+round(frequency*strength)
        names[eligible&sparse&(cluster>=low)&(cluster<high)]=name;low=high
    ellipsoid=((x-volcano['x'])/volcano['radius'])**2+((z-volcano['z'])/volcano['radius'])**2+((y-volcano['y'])/volcano['ry'])**2
    # Stay inside a section and require six original solid neighbours. Existing
    # tunnels, water, structures and ores are never pierced by the lava model.
    solid=np.isin(old_indices,stone).reshape(16,16,16)
    sealed=np.zeros((16,16,16),dtype=bool)
    sealed[2:-2,2:-2,2:-2]=(solid[1:-3,2:-2,2:-2]&solid[3:-1,2:-2,2:-2]&
        solid[2:-2,1:-3,2:-2]&solid[2:-2,3:-1,2:-2]&solid[2:-2,2:-2,1:-3]&solid[2:-2,2:-2,3:-1])
    chamber=eligible&sealed.ravel()&(ellipsoid<1)
    names[chamber]='magma';names[chamber&(ellipsoid<.65)]='lava'
    changed=names!=''
    if not changed.any():return result,count
    layers=decode(result);indices,palette=layers[0];version=palette[0].compound['version']
    for name in np.unique(names[changed]):
        states={'liquid_depth':nbt.IntTag(0)} if name=='lava' else {}
        tag=nbt.CompoundTag({'name':nbt.StringTag('minecraft:'+name),'states':nbt.CompoundTag(states),'version':version})
        slot=next((i for i,v in enumerate(palette) if v.compound==tag),None)
        if slot is None:slot=len(palette);palette.append(nbt.NamedTag(tag))
        indices[names==name]=slot
    remains=[i for i,v in enumerate(palette) if str(v.compound['name'])=='minecraft:stone' and not v.compound['states']]
    return encode(result[:3],layers),int((np.isin(old_indices,stone)&~np.isin(indices,remains)).sum())


def decorate_batch(rows):
    # Workers receive bytes and coordinates only. They never open LevelDB.
    return [(row[0],*finish_volcano(row,*decorate(row[1],*row[2:7]))) for row in rows]


def cpu_records(rows,workers):
    if workers==1:
        for row in rows:yield decorate_batch([row])[0]
        return
    # Python 3.12 Executor.map eagerly consumes an iterator. Bound the number
    # of in-flight batches so a large world never builds a huge RAM queue.
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending=deque();source=iter(rows);exhausted=False
        while pending or not exhausted:
            while len(pending)<workers*2 and not exhausted:
                batch=list(islice(source,128))
                if batch:pending.append(pool.submit(decorate_batch,batch))
                else:exhausted=True
            if pending:yield from pending.popleft().result()


def decorated_records(rows,workers,backend='cpu',notify=lambda _:None,report=None):
    global _GPU
    state=report if report is not None else {};state['backend']='cpu'
    if backend not in ('auto','cpu','gpu'):raise ValueError('演算方法はauto/cpu/gpuから選んでください。')
    if backend=='cpu' or (backend=='auto' and _AUTO_BACKENDS.get(workers)=='cpu'):
        yield from cpu_records(rows,workers);return
    source=iter(rows);first=list(islice(source,1024))
    if not first:return
    try:
        gpu=gpu_session();state['gpu_name']=gpu.name
    except Exception as error:
        _AUTO_BACKENDS[workers]='cpu';state.update(backend='cpu',fallback_reason=str(error))
        if _GPU is not None:_GPU.close();_GPU=None
        notify('GPUを利用できないためCPUで続行します。')
        yield from cpu_records(chain(first,source),workers);return
    with ProcessPoolExecutor(max_workers=workers) as codec_pool:
        batch=first;initial=True
        while batch:
            try:
                if initial and backend=='auto' and workers not in _AUTO_BACKENDS:
                    # GPU arithmetic plus parallel CPU palette conversion. Both
                    # paths include transfers/codecs but exclude cold startup.
                    gpu_batch(first[:1],gpu)
                    warm=[codec_pool.submit(decorate_batch,first[:1]) for _ in range(workers)]
                    for task in warm:task.result()
                    chunks=[first[i:i+128] for i in range(0,len(first),128)]
                    t=time.perf_counter();cpu=[item for part in codec_pool.map(decorate_batch,chunks) for item in part];cpu_time=time.perf_counter()-t
                    t=time.perf_counter();result=gpu_batch(first,gpu,codec_pool);gpu_time=time.perf_counter()-t
                    if result!=cpu:raise RuntimeError('GPU結果がCPUの保存データと一致しませんでした')
                    choice='gpu' if gpu_time<cpu_time*.9 else 'cpu';_AUTO_BACKENDS[workers]=choice
                    state.update(calibration={'cpu_seconds':cpu_time,'gpu_seconds':gpu_time},backend=choice)
                    notify(f'演算速度を比較: CPU {cpu_time:.3f}秒 / GPU {gpu_time:.3f}秒 → {choice.upper()}を使用')
                    if choice=='cpu':
                        gpu.close();_GPU=None
                        yield from cpu;yield from cpu_records(source,workers);return
                else:
                    result=gpu_batch(batch,gpu,codec_pool)
                    if initial and result[:16]!=decorate_batch(batch[:16]):raise RuntimeError('GPU結果がCPUと一致しませんでした')
                if initial:
                    state['backend']='gpu';notify(f'地盤計算: GPU {gpu.name} ＋ CPU変換{workers}並列（保存は1プロセス）')
            except Exception as error:
                _AUTO_BACKENDS[workers]='cpu';state.update(backend='cpu',fallback_reason=str(error))
                if _GPU is not None:_GPU.close();_GPU=None
                notify('GPU経路を利用できないためCPUで続行します。')
                yield from cpu_records(chain(batch,source),workers);return
            yield from result
            initial=False;batch=list(islice(source,1024))


def apply_directory(world,reference,cache,notify=print,offset=(0,0),survey=None,workers=None,backend=None):
    workers=int(os.environ.get('MAPCRAFT_GEOLOGY_WORKERS',str(min(8,max(1,(os.cpu_count() or 2)//2))))) if workers is None else workers
    backend=backend or os.environ.get('MAPCRAFT_COMPUTE_BACKEND','auto')
    if not 1<=workers<=32:raise ValueError('地盤処理の並列数は1〜32で指定してください。')
    started=time.monotonic()
    world=Path(world);survey=survey or Survey(cache,notify);db=leveldb.LevelDB(str(world/'db'),create_if_missing=False)
    batch={};changed=0;processed=0;profiles={};last=None;volcano=None
    from volcanoes import parameters,CREDITS as VOLCANO_CREDITS
    if workers>1:notify(f'地盤処理: {backend.upper()} / CPU最大{workers}並列（保存は1プロセス）')
    def records():
        nonlocal last,volcano
        for key,value in db.iterate():
            if len(key)!=10 or key[8]!=47:continue
            section=struct.unpack('b',key[9:])[0]
            if not 0<=section<=3:continue
            cx,cz=struct.unpack('<ii',key[:8]);chunk=(cx,cz)
            if last!=chunk:
                lat,lon=reference.geographic(cx*16+8+offset[0],cz*16+8+offset[1]);rock=survey.sample(lat,lon);last=chunk
                profiles[rock]=profiles.get(rock,0)+1
                volcano=parameters(reference,lat,lon)
            yield key,value,cx,cz,section,rock,offset,volcano
    try:
        compute={}
        for key,result,count in decorated_records(records(),workers,backend,notify,compute):
            if count:batch[key]=result;changed+=count
            processed+=1
            if processed%1024==0:
                if batch:db.putBatch(batch);batch={}
                notify(f'地盤反映: {processed:,} サブチャンク / {changed:,} ブロック変更')
        if batch:db.putBatch(batch)
    finally:db.close()
    record={'version':VERSION,'profiles':profiles,'changed_blocks':changed,'workers':workers,'compute':compute,'seconds':round(time.monotonic()-started,3),'scope':'Only original plain stone at Y=1..56; other blocks, air and secondary storage unchanged.','model':'Surface survey reference; depth and ore frequencies are fictional gameplay adaptation.'}
    (world/'mapcraft_geology.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
    (world/'GEOLOGY-CREDITS.txt').write_text(CREDITS+'\n'+VOLCANO_CREDITS,encoding='utf-8')
    return record

def apply_archive(path,reference,cache,notify=print,backend=None):
    path=Path(path).resolve()
    with zipfile.ZipFile(path) as archive:
        if 'mapcraft_geology.json' in archive.namelist():
            record=json.loads(archive.read('mapcraft_geology.json'))
            if record.get('version')==VERSION:notify('反映済みの地盤を再利用します。');return record
    # Never open an installed world DB. Commit the creator archive atomically.
    with tempfile.TemporaryDirectory(prefix='.geology-',dir=path.parent) as temporary:
        root=Path(temporary).resolve()
        if not root.is_relative_to(path.parent):raise ValueError('地盤処理の作業先が不正です。')
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not (root/member.filename).resolve().is_relative_to(root):raise ValueError('ワールド内のパスが不正です。')
            archive.extractall(root)
        record=apply_directory(root,reference,cache,notify,backend=backend)
        output=root/'result.mcworld'
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
            for entry in root.rglob('*'):
                if entry.is_file() and entry!=output and entry.name!='LOCK':archive.write(entry,entry.relative_to(root).as_posix())
        os.replace(output,path)
    return record

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);args=parser.parse_args()
    data=json.loads(Path(args.request).read_text(encoding='utf-8'))
    apply_archive(data['world'],MapReference.load(data['reference']),data['cache'],lambda message:print(message,flush=True),backend=data.get('backend'))
