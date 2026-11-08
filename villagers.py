"""Municipal population apportioned by mapped area, then placed in residences."""
from dataclasses import replace
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re

DEFAULT={'enabled':False,'people_per_villager':250,'outdoors_enabled':True}
CREDITS='人口: 総務省 住民基本台帳に基づく人口・人口動態及び世帯数調査（総計、2026年1月1日現在）\nhttps://www.e-stat.go.jp/stat-search/files?cycle=7&layout=dataset&stat_infid=000040479049&toukei=00200241\n境界: © OpenStreetMap contributors (ODbL-1.0)。市町村内の人口は面積で按分したゲーム用近似です。\n'

def options(value=None):
    result={**DEFAULT,**(value or {})};n=result['people_per_villager']
    if not isinstance(result['enabled'],bool) or not isinstance(result['outdoors_enabled'],bool) or isinstance(n,bool) or not isinstance(n,(float,int)) or not math.isfinite(n) or not 100<=n<=100000:
        raise ValueError('村人の人数設定は実人口100〜100,000人につき1人で指定してください。')
    return result

@lru_cache(maxsize=1)
def population():
    return json.loads((Path(__file__).parent/'municipal-population.json').read_text(encoding='utf-8'))

def normalized(name):
    name=''.join(str(name).split()).replace('ケ','ヶ').replace('驒','騨').replace('惠','恵')
    return re.sub(r'^.+郡(?=.+[町村]$)','',name).removesuffix('市')

def geometry(area):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    shape=unary_union([Polygon(r).buffer(0) for r in area['outer']])
    if area.get('inner'):shape=shape.difference(unary_union([Polygon(r).buffer(0) for r in area['inner']]))
    return shape

def match_population(area,prefectures=()):
    rows=population()['municipalities'];name=normalized(area['name']);code=str(area.get('local_authority_code',''))
    if code:
        found=[r for r in rows if r[0][:5]==code[:5]]
        if len(found)==1:return found[0]
    found=[r for r in rows if normalized(r[2])==name]
    if len(found)<=1:return found[0] if found else None
    if not prefectures:return None
    # Disambiguate e.g. Tokyo/Hiroshima 府中市. Never guess a same-name city.
    point=geometry(area).representative_point()
    for pref in prefectures:
        w,s,e,n=pref['bounds']
        if w<=point.x<=e and s<=point.y<=n and geometry(pref).covers(point):
            name=normalized(pref['name'])
            return next((r for r in found if normalized(r[1]) in (name,name+'都',name+'道',name+'府',name+'県')),None)
    return None

def compile_villagers(reference,payload,boundaries,settings=None,*,prefectures=(),owned_bounds=None,notify=lambda _:None):
    from shapely.geometry import box,Point
    from shapely.ops import transform
    from treasure import compile_treasure
    settings=options(settings)
    result={'version':1,'options':settings,'population_as_of':population()['as_of'],'plans':[],'areas':[],'unmatched':[]}
    if not settings['enabled']:return result
    # Uniform municipal density is explicit; OSM building counts do not define population.
    allowed={'yes','house','detached','residential','apartments','terrace','dormitory','semidetached_house','hotel','bungalow'}
    ids={'way:'+str(e['id']) for e in payload.get('elements',[]) if e.get('type')=='way' and e.get('tags',{}).get('building') in allowed}
    candidates=compile_treasure(replace(reference,treasure_enabled=True,treasure_probability=1),payload,namespace='villagers-v1',classify=False,per_floor=False)['plans']
    candidates=[p for p in candidates if p['source'] in ids]
    if not candidates:return result
    s,w,n,e=reference.bbox
    if owned_bounds is None:
        a,b=reference.project(n,w);c,d=reference.project(s,e);owned_bounds=(a,b,c,d)
    x0,z0,x1,z1=owned_bounds
    north,west=reference.geographic(x0,z0);south,east=reference.geographic(x1,z1)
    clip=box(max(w,west),max(s,south),min(e,east),min(n,north))
    towns=boundaries.get('municipalities',boundaries) if isinstance(boundaries,dict) else boundaries
    for area in towns:
        aw,ass,ae,an=area['bounds']
        if ae<west or aw>east or an<south or ass>north:continue
        row=match_population(area,prefectures)
        if row is None:result['unmatched'].append(area['name']);continue
        shape=geometry(area)
        if shape.is_empty or not shape.intersects(clip):continue
        latitude=shape.centroid.y
        # A local equal-area scale suffices for municipal area fractions.
        project=lambda lon,lat,z=None:(lon*math.cos(math.radians(latitude)),math.sin(math.radians(lat)))
        whole=transform(project,shape).area;part=transform(project,shape.intersection(clip)).area
        if whole<=0 or part<=0:continue
        slots=[]
        for plan in candidates:
            # At most one villager per mapped point, max eight/house. Ownership
            # uses each actual point, even for houses crossing a tile boundary.
            for index,(x,z) in enumerate(plan['points']):
                if not (x0<=x<x1 and z0<=z<z1):continue
                lat,lon=reference.geographic(x,z)
                if shape.covers(Point(lon,lat)):slots.append((plan,index))
        seed=f'{reference.map_id}/{row[0]}/{x0:.3f}/{z0:.3f}/{x1:.3f}/{z1:.3f}'
        digest=hashlib.sha256(seed.encode()).digest()
        expected=row[3]*part/whole/settings['people_per_villager']
        target=math.floor(expected)+(int.from_bytes(digest[:4],'big')/2**32<expected%1)
        slots.sort(key=lambda p:hashlib.sha256(f'{seed}/{p[0]["source"]}/{p[1]}'.encode()).digest())
        selected=slots[:target]
        for plan,index in selected:
            identity=hashlib.sha256(f'{reference.map_id}/resident/{row[0]}/{plan["source"]}/{index}'.encode()).hexdigest()[:24]
            result['plans'].append({**plan,'id':identity,'points':[plan['points'][index]],'municipality':row[2],'population_code':row[0]})
        result['areas'].append({'code':row[0],'name':row[2],'population':row[3],'expected':round(expected,3),'target':target,'selected':len(selected)})
    result['unmatched']=sorted(set(result['unmatched']))
    if result['unmatched']:notify('人口表と照合できず村人を配置しない地域: '+', '.join(result['unmatched']))
    return result
