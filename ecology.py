"""OSM habitat and large sacred-site plans; actual spawning checks game blocks."""
import hashlib
import math

DEFAULT={'wildlife_enabled':True,'density':2.,'bosses_enabled':False,'boss_min_area':25.,'boss_beacons_enabled':True}
SPECIES={
    'beach': [('turtle',5),('rabbit',1)],
    'ocean': [('cod',5),('squid',3),('dolphin',1)],
    'water': [('salmon',5),('cod',2),('squid',1)],
    'wetland': [('frog',5),('rabbit',1)],
    'mountain': [('goat',5),('rabbit',4),('llama',1)],
    'forest': [('rabbit',4),('fox',2),('wolf',1),('bee',1),('pig',2),('chicken',2),('goat',1)],
    'farm': [('cow',4),('sheep',4),('pig',2),('chicken',4),('horse',1),('donkey',1)],
    'park': [('rabbit',4),('bee',1),('cat',1),('chicken',3),('sheep',1)],
    'city': [('cat',1)]}
WATER={'cod','salmon','squid','dolphin'}
BOSS_NAMES={'shrine':'神社の守護獣','temple':'寺院の守護獣','grave':'墓所の守護獣'}

def boss_profile(area):
    # Site area is a gameplay difficulty reference, not a claim about religion.
    tier=1 if area<2000 else 2 if area<10000 else 3 if area<50000 else 4 if area<200000 else 5
    name=('piglin_brute','elder_guardian','warden','wither','ender_dragon')[tier-1]
    low,high,start,end=((50,90,25,2000),(100,200,2000,10000),(500,800,10000,50000),(900,1400,50000,200000),(200,200,200000,1000000))[tier-1]
    health=round(low+(high-low)*max(0,min(1,(area-start)/(end-start))))
    return {'tier':tier,'type':'minecraft:'+name,'health':health,'mode':'end_portal' if tier==5 else 'pool' if tier==2 else 'land'}

def options(value=None):
    result={**DEFAULT,**(value or {})}
    for k in ('wildlife_enabled','bosses_enabled','boss_beacons_enabled'):
        if not isinstance(result[k],bool):raise ValueError('動物・ボスの設定が不正です。')
    result.pop('respawn',None)
    for k,lo,hi in (('density',0,3),('boss_min_area',25,10000000)):
        v=result[k]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError('動物の多さは0〜300%、ボス対象の面積は25〜10,000,000m²で指定してください。')
    return result

def fraction(key):return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big')/2**64
def identity(key):return hashlib.sha256(key.encode()).hexdigest()[:24]

def reuse_plan(compiled,settings):
    if compiled is None:return None
    # Beacons affect saved blocks, not habitat selection or boss locations.
    without_beacons=lambda profile:{k:v for k,v in profile.items() if k!='boss_beacons_enabled'}
    if without_beacons(compiled.get('options',{}))!=without_beacons(settings):return None
    return {**compiled,'options':dict(settings)}

def compile_mobs(reference,payload,settings=None,*,owned_bounds=None):
    from shapely.geometry import Polygon,LineString,Point,box
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
    from municipalities import join_rings
    settings=options(settings)
    result={'version':1,'map_id':reference.map_id,'options':settings,'groups':[],'bosses':[],
            'source':'OpenStreetMap','model':'OSM habitats and game adaptation, not actual animal census.'}
    if not settings['wildlife_enabled'] and not settings['bosses_enabled']:return result
    s,w,n,e=reference.bbox
    if owned_bounds is None:
        x0,z0=reference.project(n,w);x1,z1=reference.project(s,e);owned_bounds=(x0,z0,x1,z1)
    x0,z0,x1,z1=owned_bounds;coverage=box(x0,z0,x1,z1)
    lat,_=reference.geographic((x0+x1)/2,(z0+z1)/2)
    metre=reference.scale
    if reference.projection=='national_mercator':metre*=math.cos(math.radians(reference.origin[0]))/math.cos(math.radians(lat))
    cell=128*metre
    if reference.projection=='national_mercator':cell=math.gcd(128,round(x1-x0),round(z1-z0))
    nodes={e['id']:e for e in payload.get('elements',[]) if e.get('type')=='node' and 'lat' in e}
    habitats=[];sites=[];obstacles=[];religious=[];buildings=[]
    def points(element):
        geom=element.get('geometry')
        values=geom if geom else [nodes[i] for i in element.get('nodes',[]) if i in nodes]
        return [reference.project(v['lat'],v['lon']) for v in values if v and 'lat' in v]
    for item in payload.get('elements',[]):
        tags=item.get('tags',{});kind=item.get('type');shape=None
        if kind=='node' and 'lat' in item:shape=Point(*reference.project(item['lat'],item['lon']))
        elif kind=='way':
            p=points(item)
            if len(p)>=4 and p[0]==p[-1]:shape=Polygon(p).buffer(0)
            elif len(p)>=2:shape=LineString(p)
        elif kind=='relation' and tags.get('type') in ('multipolygon','boundary'):
            outer=[];inner=[]
            for member in item.get('members',[]):
                p=points(member)
                if p:(inner if member.get('role')=='inner' else outer).append(p)
            try:
                shape=unary_union([Polygon(r).buffer(0) for r in join_rings(outer)])
                if inner:shape=shape.difference(unary_union([Polygon(r).buffer(0) for r in join_rings(inner)]))
            except ValueError:continue
        if shape is None or shape.is_empty:continue
        natural=tags.get('natural');land=tags.get('landuse');water=tags.get('water');leisure=tags.get('leisure')
        if tags.get('building') not in (None,'no') and shape.area:
            obstacles.append(shape.buffer(3*metre));buildings.append((item,shape))
        if tags.get('highway') and tags['highway'] not in ('footway','path','steps','pedestrian'):
            obstacles.append(shape.buffer(5*metre))
        habitat=None;radius=0
        if natural in ('beach','sand'):habitat='beach'
        elif natural=='coastline':habitat='ocean';radius=100*metre
        elif natural in ('peak','ridge','saddle','fell','scree','bare_rock'):habitat='mountain';radius=600*metre if not shape.area else 0
        elif natural=='wetland':habitat='wetland'
        elif natural=='water' or water or tags.get('waterway') in ('riverbank','river','stream'):
            habitat='ocean' if water in ('sea','ocean') else 'water';radius=6*metre if not shape.area else 0
        elif natural=='wood' or land=='forest':habitat='forest'
        elif land in ('farmland','farmyard','meadow','grass','orchard','allotments'):habitat='farm'
        elif leisure in ('park','garden','nature_reserve','recreation_ground'):habitat='park'
        elif land=='residential':habitat='city'
        if habitat:
            area=shape.buffer(radius) if radius else shape
            if area.area and area.intersects(coverage):habitats.append((habitat,area))
            if natural=='coastline':habitats.append(('beach',shape.buffer(24*metre)))
        religion=tags.get('religion');building=tags.get('building')
        sacred='shrine' if religion=='shinto' or building=='shrine' else 'temple' if religion=='buddhist' or building=='temple' else None
        if sacred:religious.append((sacred,shape.representative_point(),tags.get('name:ja') or tags.get('name')))
        grave=land=='cemetery' or tags.get('amenity')=='grave_yard' or tags.get('historic') in ('tomb','cemetery')
        if shape.area and (sacred or grave or land=='religious'):
            sites.append({'kind':'grave' if grave else sacred,'shape':shape,'source':f'{kind}:{item["id"]}',
                          'name':tags.get('name:ja') or tags.get('name'),'footprint_only':tags.get('building') not in (None,'no')})
    registered={v['source'] for v in sites}
    for item,shape in buildings:
        source=f'{item["type"]}:{item["id"]}'
        if source in registered:continue
        nearby=[v for v in religious if shape.covers(v[1])]
        if nearby:
            sacred,_,name=min(nearby,key=lambda v:(v[0],v[2] or ''))
            sites.append({'kind':sacred,'shape':shape,'source':source,'name':name,'footprint_only':True})
    blocked=unary_union(obstacles) if obstacles else None
    if settings['wildlife_enabled'] and settings['density']>0 and habitats:
        shapes=[v[1] for v in habitats];tree=STRtree(shapes)
        rank={k:i for i,k in enumerate(('beach','ocean','water','wetland','mountain','forest','farm','park','city'))}
        for gz in range(math.floor(z0/cell),math.ceil(z1/cell)):
            for gx in range(math.floor(x0/cell),math.ceil(x1/cell)):
                region=box(gx*cell,gz*cell,(gx+1)*cell,(gz+1)*cell).intersection(coverage)
                if region.is_empty or region.area<cell*cell*.01:continue
                seed=f'{reference.map_id}/habitat/v2/{cell:.6f}/{gx}/{gz}'
                choices=[]
                for index in tree.query(region):
                    habitat,shape=habitats[int(index)];part=shape.intersection(region)
                    if blocked is not None:part=part.difference(blocked)
                    if part.area<=0:continue
                    point=part.representative_point();choices.append((rank[habitat],habitat,point,part))
                if not choices:continue
                # A tiny stream must not displace a whole field's livestock.
                # Prefer the habitat covering most of the available cell.
                choices.sort(key=lambda v:(-v[3].area,v[0],v[2].x,v[2].y))
                _,habitat,anchor,part=choices[0]
                # Sparse city cats; a few animals per habitat cell, never per block.
                rate=(.12 if habitat=='city' else .7)*settings['density']*min(1,region.area/((128*metre)**2))
                if fraction(seed+'/draw')>=min(1,rate):continue
                weights=SPECIES[habitat];draw=fraction(seed+'/type')*sum(v for _,v in weights)
                species=weights[-1][0]
                for name,weight in weights:
                    draw-=weight
                    if draw<0:species=name;break
                spots=[]
                for i in range(16):
                    x=anchor.x+(fraction(seed+f'/x/{i}')-.5)*36*metre
                    z=anchor.y+(fraction(seed+f'/z/{i}')-.5)*36*metre
                    if part.covers(Point(x,z)) and reference.contains(x,z):spots.append([math.floor(x),math.floor(z)])
                spots=list(dict.fromkeys(map(tuple,[[math.floor(anchor.x),math.floor(anchor.y)],*spots])))
                spots=[list(p) for p in spots if x0<=p[0]<x1 and z0<=p[1]<z1 and reference.contains(*p)]
                if not spots:continue
                count=1+int(fraction(seed+'/count')*2*max(1,rate))
                result['groups'].append({'id':identity(seed),'habitat':habitat,'type':'minecraft:'+species,
                    'points':spots,'count':min(4,count),'aquatic':species in WATER,'width':1.5 if species in ('horse','donkey','cow','llama','turtle','dolphin') else .9,
                    'min_depth':3 if species=='dolphin' else 2,'seed':int(fraction(seed+'/variant')*2**32)})
    if settings['bosses_enabled']:
        accepted=[]
        sites.sort(key=lambda v:(-v['shape'].area,v['source']))
        for site in sites:
            shape=site['shape'];anchor=shape.representative_point()
            if not (x0<=anchor.x<x1 and z0<=anchor.y<z1) or not reference.contains(anchor.x,anchor.y):continue
            area=shape.area/metre**2
            if area<settings['boss_min_area']:continue
            if site['kind'] is None:
                nearby=[v for v in religious if shape.covers(v[1])]
                if not nearby:continue
                chosen=min(nearby,key=lambda v:(anchor.distance(v[1]),v[0],v[2] or ''))
                site={**site,'kind':chosen[0],'name':site['name'] or chosen[2]}
            if any(other.covers(anchor) or shape.intersection(other).area>shape.area*.5 for other in accepted):continue
            accepted.append(shape)
            free=shape.buffer(12*metre) if site.get('footprint_only') else shape.buffer(-3*metre)
            if blocked is not None:free=free.difference(blocked)
            free=free.intersection(coverage)
            if free.is_empty:continue
            seed=f'{reference.map_id}/boss/{site["source"]}'
            spots=[]
            parts=sorted(getattr(free,'geoms',[free]),key=lambda p:-p.area)
            for part in parts[:8]:
                p=part.representative_point();spots.append([math.floor(p.x),math.floor(p.y)])
            minx,minz,maxx,maxz=free.bounds
            for i in range(64):
                x=minx+(maxx-minx)*fraction(seed+f'/x/{i}');z=minz+(maxz-minz)*fraction(seed+f'/z/{i}')
                if free.covers(Point(x,z)):spots.append([math.floor(x),math.floor(z)])
                if len(spots)>=16:break
            spots=[p for p in spots if reference.contains(*p)]
            if not spots:continue
            result['bosses'].append({'id':identity(seed),'kind':site['kind'],**boss_profile(area),
                'name':(site['name'] or BOSS_NAMES[site['kind']])+'の守護者' if site['name'] else BOSS_NAMES[site['kind']],
                'area_m2':round(area),'area_basis':'building_footprint' if site.get('footprint_only') else 'site','source':site['source'],'points':spots,'count':1,'width':2.2})
    return result
