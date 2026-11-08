"""Real JMA volcano positions; fictional, deliberately bounded gameplay effects."""
from functools import lru_cache
import json
import math
from pathlib import Path

RADIUS_M=10000
CREDITS='活火山位置: 気象庁 日本活火山総覧 https://www.data.jma.go.jp/vois/data/filing/bulletin/catalog/appendix/v_active.html\nマグマだまり、影響半径、鉱物の頻度はゲーム用モデルであり、実際の地下構造・鉱床を示しません。\n'

@lru_cache(maxsize=1)
def grid():
    result={}
    for item in json.loads((Path(__file__).parent/'active-volcanoes.json').read_text(encoding='utf-8'))['volcanoes']:
        result.setdefault((math.floor(item['lat']),math.floor(item['lon'])),[]).append(item)
    return result

def nearby(lat,lon):
    best=None
    a,b=math.floor(lat),math.floor(lon)
    for x in range(a-1,a+2):
        for y in range(b-1,b+2):
            for item in grid().get((x,y),()):
                distance=math.hypot((lat-item['lat'])*111195,(lon-item['lon'])*111195*math.cos(math.radians(lat)))
                if distance<RADIUS_M and (best is None or distance<best['distance_m']):
                    best={**item,'distance_m':round(distance,2),'strength':max(1,round(1000*(1-distance/RADIUS_M)))}
    return best

def parameters(reference,lat,lon):
    item=nearby(lat,lon)
    if item is None:return None
    x,z=reference.project(item['lat'],item['lon'])
    # Small sealed pockets near the representative summit, not a real magma model.
    scale=reference.scale
    if reference.projection=='national_mercator':scale*=math.cos(math.radians(reference.origin[0]))/math.cos(math.radians(lat))
    return {**item,'x':x,'z':z,'radius':max(16.,100*scale),'y':20+(item['id']%7),'ry':5}
