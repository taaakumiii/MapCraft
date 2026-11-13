"""Shared GUI/CLI region catalogue and cached official land/coast geometry."""
import hashlib
import json
import os
from pathlib import Path
import urllib.request
import uuid
import zipfile

PREFECTURES=dict(enumerate(('北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県').split(),1))
REGIONS={
    '北海道':('hokkaido',(1,)), '東北':('tohoku',tuple(range(2,8))),
    '関東':('kanto',tuple(range(8,15))), '中部':('chubu',tuple(range(15,24))),
    '近畿':('kinki',tuple(range(24,31))), '中国':('chugoku',tuple(range(31,36))),
    '四国':('shikoku',tuple(range(36,40))), '九州・沖縄':('kyushu-okinawa',tuple(range(40,48))),
}
SCOPE_KINDS=('日本全国','都道府県','地方','いま選択した範囲')
SOURCE_PAGE='https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2026.html'
SOURCE_BASE='https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2026/'


def selection(kind='日本全国',name=''):
    if kind=='日本全国':return {'kind':'national','id':'japan','label':'日本全国','codes':list(range(1,48))}
    if kind=='都道府県':
        code=next((code for code,label in PREFECTURES.items() if label==name),None)
        if code is not None:return {'kind':'prefecture','id':f'pref-{code:02d}','label':name,'codes':[code]}
    if kind=='地方' and name in REGIONS:
        ident,codes=REGIONS[name]
        return {'kind':'region','id':ident,'label':name,'codes':list(codes)}
    if kind=='いま選択した範囲':return {'kind':'map','id':'selected','label':'選択範囲','codes':[]}
    raise ValueError('生成範囲を選択してください。')


def normalize(value):
    if not isinstance(value,dict):raise ValueError('生成範囲の指定が不正です。')
    kind=value.get('kind')
    if kind=='national':return selection()
    if kind=='map':return selection('いま選択した範囲')
    if kind=='prefecture':
        return selection('都道府県',next((name for code,name in PREFECTURES.items() if value.get('id')==f'pref-{code:02d}'),''))
    if kind=='region':
        return selection('地方',next((name for name,(ident,_) in REGIONS.items() if value.get('id')==ident),''))
    raise ValueError('生成範囲の指定が不正です。')


def default_project_name(value):
    item=normalize(value)
    return 'MapCraftJapan' if item['kind']=='national' else 'MapCraft_'+item['id']


def export_filename(config):
    label=config.get('scope_selection',{}).get('label','日本全国')
    return f'MapCraft_{label}_{1/config["scale"]:g}m.mcworld'


def read_land(path,code):
    from shapely.geometry import shape
    from shapely.ops import unary_union
    with zipfile.ZipFile(path) as archive:
        names=[name for name in archive.namelist() if name.lower().endswith('.geojson')]
        if len(names)!=1:raise ValueError('行政区域データにGeoJSONがありません。再取得してください。')
        payload=json.loads(archive.read(names[0]))
    features=payload.get('features',[])
    # Validate the prefecture instead of trusting a renamed ZIP.
    if not features or any(f.get('properties',{}).get('N03_001')!=PREFECTURES[code] for f in features):
        raise ValueError('行政区域データの都道府県が一致しません。')
    geometry=unary_union([shape(f['geometry']).buffer(0) for f in features if f.get('geometry')])
    if geometry.is_empty:raise ValueError('行政区域の陸地を読み取れません。')
    return geometry


def land_file(cache,code,notify,cancel=None):
    path=Path(cache)/f'N03-20260101_{code:02d}_GML.zip'
    if path.is_file():return path
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
    notify(f'行政区域データを取得: {PREFECTURES[code]}')
    try:
        request=urllib.request.Request(SOURCE_BASE+path.name,headers={'User-Agent':'MapCraft regional world builder'})
        with urllib.request.urlopen(request,timeout=60) as response,temporary.open('wb') as output:
            while chunk:=response.read(1024*1024):
                if cancel and cancel.exists():raise InterruptedError('行政区域データの取得を中断しました。')
                output.write(chunk)
        read_land(temporary,code)
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return path


def coverage(value,prefectures,cache,scale,notify=lambda _:None,cancel=None):
    """Land + ~500m coastal fringe, clipped to selected OSM admin boundaries."""
    from shapely.geometry import Polygon
    from shapely.ops import transform,unary_union
    from national_backend import forward,inverse
    item=normalize(value);parts=[];admins=[];sources=[]
    for code in item['codes']:
        if cancel and cancel.exists():raise InterruptedError('範囲の計画を中断しました。')
        key=f'JP-{code:02d}'
        if key not in prefectures:raise ValueError(f'{PREFECTURES[code]}のOSM境界がありません。日本全国の索引を使用してください。')
        path=land_file(cache,code,notify,cancel);notify(f'陸地と沿岸を計画: {PREFECTURES[code]}')
        parts.append(read_land(path,code))
        area=prefectures[key];outer=unary_union([Polygon(r).buffer(0) for r in area['outer']])
        if area.get('inner'):outer=outer.difference(unary_union([Polygon(r).buffer(0) for r in area['inner']]))
        admins.append(outer)
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        sources.append({'prefecture':code,'file':path.name,'sha256':digest,'url':SOURCE_BASE+path.name})
    project=lambda g:transform(lambda lon,lat,z=None:forward(lat,lon,scale),g)
    land=project(unary_union(parts));admin=project(unary_union(admins))
    buffered=land.buffer(500*scale).intersection(admin).union(land)
    result=transform(lambda x,z,*_:tuple(reversed(inverse(x,z,scale))),buffered)
    return result,{'name':'国土数値情報 行政区域 N03 2026','url':SOURCE_PAGE,'license':'CC BY 4.0',
                   'coast_buffer_m':500,'modified':'陸地を全国共通座標へ投影し、約500mの沿岸を追加。OSM行政境界で沿岸を切り抜き。','files':sources}
