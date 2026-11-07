"""Bedrock GPS pack generation and a read-only content-log receiver."""
from dataclasses import dataclass, asdict
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import time
import uuid
import zipfile
from core import validate_bbox

MARKER='MAPCRAFT_GPS '

@dataclass
class MapReference:
    map_id: str
    bbox: tuple
    scale: float
    span_x: float
    span_z: float
    version: int=1
    treasure_enabled: bool=True
    treasure_probability: float=.25
    projection: str='local'
    origin: tuple=(35.,135.)
    beds_enabled: bool=True
    beds_probability: float=.25
    furniture_baked: bool=False

    @classmethod
    def from_generation(cls,data,identity=''):
        if data.get('edition')!='bedrock':raise ValueError('Windows統合版の生成情報を選んでください。')
        bbox=data['bbox'];scale=float(data['scale']);args=data.get('command',[])
        # The CLI rounds coordinates to seven decimals. Use the actual engine input.
        for i,arg in enumerate(args):
            if arg.startswith('--bbox='):bbox=[float(v) for v in arg.split('=',1)[1].split(',')]
            if arg=='--rotation' and float(args[i+1])!=0:raise ValueError('回転済みのワールドは現在地連携に対応していません。')
            if arg=='--projection' and args[i+1]!='local':raise ValueError('この投影方式は現在地連携に対応していません。')
        s,w,n,e=validate_bbox(tuple(float(v) for v in bbox))
        if not .05<=scale<=4:raise ValueError('縮尺が不正です。')
        radius=6371000.
        a=math.sin(math.radians(n-s)/2)**2
        height=radius*2*math.atan2(math.sqrt(a),math.sqrt(1-a))
        a=math.cos(math.radians((s+n)/2))**2*math.sin(math.radians(e-w)/2)**2
        width=radius*2*math.atan2(math.sqrt(a),math.sqrt(1-a))
        sx,sz=math.floor(width)*scale,math.floor(height)*scale
        if min(sx,sz)<1:raise ValueError('範囲が小さすぎます。')
        raw=json.dumps([s,w,n,e,scale,identity],ensure_ascii=True)
        options=data.get('treasure',{})
        beds=data.get('beds',{})
        return cls(str(uuid.uuid5(uuid.NAMESPACE_URL,'MapCraft/GPS/'+raw)),(s,w,n,e),scale,sx,sz,
                   treasure_enabled=options.get('enabled',True),treasure_probability=options.get('probability',.25),
                   beds_enabled=beds.get('enabled',True),beds_probability=beds.get('probability',.25),furniture_baked=data.get('furniture_baked',False))

    @classmethod
    def load(cls,data):
        item=cls(**{k:data[k] for k in ('map_id','bbox','scale','span_x','span_z')})
        item.treasure_enabled=data.get('treasure_enabled',True)
        item.treasure_probability=data.get('treasure_probability',.25)
        item.projection=data.get('projection','local');item.origin=tuple(data.get('origin',(35.,135.)))
        item.beds_enabled=data.get('beds_enabled',True);item.beds_probability=data.get('beds_probability',.25)
        item.furniture_baked=data.get('furniture_baked',False)
        if not isinstance(item.furniture_baked,bool):raise ValueError('家具の生成方式が不正です。')
        if item.projection not in ('local','national_mercator'):raise ValueError('未知の投影方式です。')
        if len(item.origin)!=2 or not all(math.isfinite(v) for v in item.origin):raise ValueError('原点が不正です。')
        uuid.UUID(item.map_id);validate_bbox(item.bbox)
        if not all(math.isfinite(v) and v>0 for v in (item.scale,item.span_x,item.span_z)):
            raise ValueError('現在地連携の座標設定が不正です。')
        if not isinstance(item.treasure_enabled,bool) or not isinstance(item.treasure_probability,(int,float)) or not 0<=item.treasure_probability<=1:
            raise ValueError('チェスト配置の設定が不正です。')
        if not isinstance(item.beds_enabled,bool) or isinstance(item.beds_probability,bool) or not isinstance(item.beds_probability,(int,float)) or not math.isfinite(item.beds_probability) or not 0<=item.beds_probability<=1:
            raise ValueError('ベッド配置の設定が不正です。')
        return item

    def geographic(self,x,z):
        if self.projection=='national_mercator':
            lat,lon=self.origin;k=self.scale*math.cos(math.radians(lat));r=6371000.
            y=r*math.log(math.tan(math.pi/4+math.radians(lat)/2))-z/k
            return math.degrees(2*math.atan(math.exp(y/r))-math.pi/2),lon+math.degrees(x/(r*k))
        s,w,n,e=self.bbox
        return n-z/self.span_z*(n-s), w+x/self.span_x*(e-w)

    def project(self,lat,lon):
        if self.projection=='national_mercator':
            a,b=self.origin;k=self.scale*math.cos(math.radians(a));r=6371000.
            y=lambda p:r*math.log(math.tan(math.pi/4+math.radians(p)/2))
            return r*math.radians(lon-b)*k,-(y(lat)-y(a))*k
        s,w,n,e=self.bbox
        return (lon-w)/(e-w)*self.span_x,(n-lat)/(n-s)*self.span_z

    def contains(self,x,z):
        if self.projection=='national_mercator':
            lat,lon=self.geographic(x,z);s,w,n,e=self.bbox
            return s<=lat<=n and w<=lon<=e
        return 0<=x<=self.span_x and 0<=z<=self.span_z

    def pack_files(self):
        header=str(uuid.uuid5(uuid.UUID(self.map_id),'behavior-header'))
        module=str(uuid.uuid5(uuid.UUID(self.map_id),'script-module'))
        manifest={'format_version':2,'header':{'name':'MapCraft GPS '+self.map_id[:8],
            'description':'現在地・方位を表示し、PCのMapCraftミニマップと連携します。',
            'uuid':header,'version':[0,3,0],'min_engine_version':[1,21,40]},
            'modules':[{'type':'script','language':'javascript','uuid':module,'version':[0,3,0],'entry':'scripts/main.js'}],
            'dependencies':[{'module_name':'@minecraft/server','version':'1.15.0'}]}
        config=json.dumps(asdict(self),ensure_ascii=False,separators=(',',':'))
        source=(Path(__file__).resolve().parent/'gps.js').read_text(encoding='utf-8').replace('/*MAPCRAFT_CONFIG*/',config)
        files={'manifest.json':json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8'),
               'scripts/main.js':source.encode('utf-8'),
               'mapcraft_tracking.json':json.dumps(asdict(self),ensure_ascii=False,indent=2).encode('utf-8')}
        return header,files

def write_pack(reference,destination):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    _,files=reference.pack_files()
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as out:
        for name,value in files.items():out.writestr(name,value)
    return destination

def embed_pack(world_file,reference):
    """Change only a freshly generated archive; stream its database unchanged."""
    world_file=Path(world_file);temporary=world_file.with_suffix('.gps.tmp')
    header,files=reference.pack_files();prefix='behavior_packs/mapcraft_gps/'
    try:
        with zipfile.ZipFile(world_file) as original,zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as out:
            entries=[]
            if 'world_behavior_packs.json' in original.namelist():
                entries=json.loads(original.read('world_behavior_packs.json').decode('utf-8-sig'))
            entries=[entry for entry in entries if entry.get('pack_id')!=header]
            entries.append({'pack_id':header,'version':[0,3,0]})
            for item in original.infolist():
                if item.filename in ('world_behavior_packs.json','mapcraft_tracking.json') or item.filename.startswith(prefix):continue
                with original.open(item) as src,out.open(item,'w') as dst:shutil.copyfileobj(src,dst,1024*1024)
            for name,value in files.items():out.writestr(prefix+name,value)
            out.writestr('world_behavior_packs.json',json.dumps(entries))
            out.writestr('mapcraft_tracking.json',json.dumps(asdict(reference)))
        os.replace(temporary,world_file)
    finally:
        if temporary.exists():temporary.unlink()

def load_reference(path):
    path=Path(path)
    if path.suffix.lower()=='.mcaddon':
        with zipfile.ZipFile(path) as bundle:
            for name in bundle.namelist():
                if name.endswith('.mcpack'):
                    with zipfile.ZipFile(io.BytesIO(bundle.read(name))) as pack:
                        if 'mapcraft_tracking.json' in pack.namelist():
                            return MapReference.load(json.loads(pack.read('mapcraft_tracking.json')))
        raise ValueError('このアドオンにはワールド座標情報がありません。generation.json を選んでください。')
    if path.suffix.lower() in ('.mcworld','.mcpack'):
        with zipfile.ZipFile(path) as archive:
            if 'mapcraft_tracking.json' not in archive.namelist():
                raise ValueError('連携情報が入っていません。このワールドと同じフォルダーの generation.json を選んでください。')
            return MapReference.load(json.loads(archive.read('mapcraft_tracking.json')))
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if 'map_id' in data:return MapReference.load(data)
    return MapReference.from_generation(data,path.parent.name)

def parse_packet(line,reference,now=None):
    pos=line.find(MARKER)
    if pos<0:return None
    try:
        value,_=json.JSONDecoder().raw_decode(line[pos+len(MARKER):].strip())
        if not isinstance(value,dict):return None
        if value.get('map')!=reference.map_id:return None
        for key in ('x','y','z','yaw','t'):
            if isinstance(value.get(key),bool) or not isinstance(value.get(key),(float,int)) or not math.isfinite(value[key]):return None
        if abs((time.time() if now is None else now)-value['t']/1000)>8:return None
        if max(abs(value['x']),abs(value['z']))>30000000 or abs(value['y'])>4096:return None
        if value.get('dimension')!='minecraft:overworld':return None
        value['lat'],value['lon']=reference.geographic(value['x'],value['z'])
        value['inside']=reference.contains(value['x'],value['z'])
        value['player']=str(value.get('player','プレイヤー'))[:80]
        value['pid']=str(value.get('pid',''))[:100]
        return value
    except (ValueError,TypeError,KeyError):return None

def log_directories():
    roaming=Path(os.environ.get('APPDATA',str(Path.home())))
    local=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))
    roots=[roaming/'Minecraft Bedrock/logs',local/'Packages/Microsoft.MinecraftUWP_8wekyb3d8bbwe/LocalState/logs',
           local/'Packages/Microsoft.MinecraftUWP_8wekyb3d8bbwe/LocalState/games/com.mojang/logs']
    users=roaming/'Minecraft Bedrock/Users'
    if users.exists():
        for user in users.iterdir():
            if user.is_dir():roots += [user/'logs',user/'games/com.mojang/logs']
    return roots

class ContentLogReader:
    def __init__(self,reference,custom=None):
        self.reference=reference;self.custom=Path(custom) if custom else None
        self.path=None;self.offset=0;self.buffer=b'';self.last_scan=0

    def poll(self):
        if time.monotonic()-self.last_scan>2 or self.path is None:
            self.last_scan=time.monotonic();candidates=[]
            if self.custom:candidates=[self.custom] if self.custom.exists() else []
            else:
                for folder in log_directories():
                    if not folder.exists():continue
                    for path in folder.iterdir():
                        if path.is_file() and path.suffix.lower() in ('.txt','.log') and 'contentlog' in path.name.lower():candidates.append(path)
            if candidates:
                newest=max(candidates,key=lambda p:p.stat().st_mtime)
                if newest!=self.path:
                    self.path=newest;self.offset=newest.stat().st_size;self.buffer=b''
        if self.path is None:return []
        try:
            size=self.path.stat().st_size
            if size<self.offset:self.offset=0;self.buffer=b''
            with self.path.open('rb') as stream:
                stream.seek(self.offset);chunk=stream.read(1024*1024);self.offset=stream.tell()
            self.buffer+=chunk
            lines=self.buffer.split(b'\n');self.buffer=lines.pop()[-65536:]
            packets=[]
            for line in lines:
                packet=parse_packet(line.decode('utf-8',errors='replace'),self.reference)
                if packet:packets.append(packet)
            return packets
        except OSError:return []
