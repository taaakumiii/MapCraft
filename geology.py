"""GSJ surface geology and GSI artificial landform sampling, with disk caches."""
from functools import lru_cache
import io
import json
import math
import tempfile
import os
from pathlib import Path
import urllib.error
import urllib.request
from PIL import Image, ImageDraw

GSJ='https://gbank.gsj.jp/seamless/v2/api/1.3/'
GSI='https://cyberjapandata.gsi.go.jp/xyz/experimental_landformclassification2/'
VERSION=2
CREDITS='地質: 産総研地質調査総合センター「20万分の1日本シームレス地質図V2」を加工。\nhttps://gsj-seamless.jp/seamless/v2/api/1.3/\n人工地形: 国土地理院「地形分類（人工地形）」を加工。\nhttps://www.gsi.go.jp/bousaichiri/lfc_index.html\n地下の深さ・層構成・鉱石の確率はMapCraftのゲーム用設定であり、実際の三次元地質・鉱床・安全性を示しません。\n'

def pixel(lat,lon,zoom):
    size=256*2**zoom
    return (lon+180)/360*size,(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*size

def profile(legend):
    text=legend.get('lithology_en','').lower();group=legend.get('group_en','').lower()
    if not text:return 'unknown'
    if 'granite' in text or 'granodior' in text or 'tonalite' in text:return 'granite'
    if 'basalt' in text or 'gabbro' in text:return 'basalt'
    if any(s in text for s in ('andesite','dacite','rhyolite','volcanic','pyroclastic')):return 'volcanic'
    if 'limestone' in text or 'marble' in text:return 'limestone'
    if 'sandstone' in text:return 'sandstone'
    if 'mudstone' in text or 'siltstone' in text or 'shale' in text:return 'mudstone'
    if 'metamorphic' in group or any(s in text for s in ('schist','gneiss')):return 'metamorphic'
    if any(s in text for s in ('deposits','sediments','gravel','alluvium')):return 'sediment'
    if 'igneous' in group:return 'volcanic'
    return 'sedimentary'

class Survey:
    def __init__(self,cache,notify=print):
        self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True);self.notify=notify
        legends=json.loads(self.fetch('gsj-legend.json',GSJ+'legend.json'))
        self.colours={(v['r'],v['g'],v['b']):profile(v) for v in legends}
    def fetch(self,name,url,missing=False):
        path=self.cache/name
        if path.exists():return path.read_bytes()
        self.notify('地盤データ取得: '+name)
        request=urllib.request.Request(url,headers={'User-Agent':'MapCraft/0.12 (Minecraft geography tool)'})
        try:
            with urllib.request.urlopen(request,timeout=25) as response:payload=response.read()
        except urllib.error.HTTPError as error:
            if not missing or error.code!=404:raise RuntimeError('地盤データを取得できません。保存済みの生成を再試行してください: '+url) from error
            payload=b'{"type":"FeatureCollection","features":[]}'
        # Validate before caching: a proxy error must never masquerade as data.
        if name.endswith('.png'):
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                if image.size!=(256,256):raise ValueError('地質タイルのサイズが不正です。')
        else:json.loads(payload)
        with tempfile.NamedTemporaryFile(dir=self.cache,prefix=path.name+'.',suffix='.writing',delete=False) as stream:
            temporary=Path(stream.name);stream.write(payload)
        try:os.replace(temporary,path)
        finally:
            if temporary.exists():temporary.unlink()
        return payload
    @lru_cache(maxsize=64)
    def geology(self,x,y):
        data=self.fetch(f'gsj-13-{x}-{y}.png',GSJ+f'tiles/13/{y}/{x}.png?layer=g&type=original')
        return Image.open(io.BytesIO(data)).convert('RGBA')
    @lru_cache(maxsize=64)
    def artificial(self,x,y):
        data=json.loads(self.fetch(f'gsi-14-{x}-{y}.json',GSI+f'14/{x}/{y}.geojson',missing=True))
        mask=Image.new('L',(256,256),0)
        for feature in data.get('features',[]):
            code=str(feature.get('properties',{}).get('code',''))
            value=1 if code in {'11004','11005','11006','11007','11014','4010201'} else 2 if code in {'11008','4010101'} else 0
            if not value:continue
            geometry=feature.get('geometry') or {};kind=geometry.get('type');coordinates=geometry.get('coordinates',[])
            polygons=coordinates if kind=='MultiPolygon' else [coordinates] if kind=='Polygon' else []
            for polygon in polygons:
                local=Image.new('L',(256,256),0);draw=ImageDraw.Draw(local)
                for i,ring in enumerate(polygon):
                    points=[(pixel(p[1],p[0],14)[0]-x*256,pixel(p[1],p[0],14)[1]-y*256) for p in ring]
                    if len(points)>=3:draw.polygon(points,fill=value if i==0 else 0)
                mask.paste(local,(0,0),local.point(lambda v:255 if v else 0))
        return mask
    def sample(self,lat,lon):
        px,py=pixel(lat,lon,13);colour=self.geology(int(px//256),int(py//256)).getpixel((int(px)%256,int(py)%256))
        rock=self.colours.get(colour[:3],'unknown') if colour[3] else 'unknown'
        px,py=pixel(lat,lon,14);artificial=self.artificial(int(px//256),int(py//256)).getpixel((int(px)%256,int(py)%256))
        return ('fill' if artificial==1 else 'drained' if artificial==2 else rock)
