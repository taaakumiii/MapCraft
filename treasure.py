"""Create deterministic 25%-of-buildings treasure plans from real OSM land use."""
import hashlib
import json
import math
from pathlib import Path

EXCLUDED={'no','roof','carport','garage','garages','shed','greenhouse','glasshouse',
          'silo','storage_tank','bridge','construction','ruins','ruined','service',
          'toilets','transformer_tower','water_tower','container','bunker'}
FARMS={'farmland','farmyard','orchard','vineyard','allotments','meadow'}
URBAN={'commercial','retail','residential'}


def inside(x,z,ring):
    hit=False
    for (ax,az),(bx,bz) in zip(ring,ring[1:]):
        if (az>z)!=(bz>z) and x<(bx-ax)*(z-az)/(bz-az)+ax:hit=not hit
    return hit


def edge_distance(x,z,ring):
    result=float('inf')
    for (ax,az),(bx,bz) in zip(ring,ring[1:]):
        dx,dz=bx-ax,bz-az
        t=max(0,min(1,((x-ax)*dx+(z-az)*dz)/(dx*dx+dz*dz))) if dx or dz else 0
        result=min(result,math.hypot(x-ax-t*dx,z-az-t*dz))
    return result


def candidates(ring):
    xs=[p[0] for p in ring];zs=[p[1] for p in ring]
    west,east,south,north=min(xs),max(xs),min(zs),max(zs)
    result=[]
    # Interior block centers at least 1.5 blocks from the mapped wall. Sampling
    # also works for concave buildings whose polygon centroid lies outdoors.
    step=max(1,math.ceil(max(east-west,north-south)/18))
    for z in range(math.floor(south),math.ceil(north),step):
        for x in range(math.floor(west),math.ceil(east),step):
            if inside(x+.5,z+.5,ring):
                margin=edge_distance(x+.5,z+.5,ring)
                if margin>=1.5:result.append((margin,x,z))
    if not result:return []
    result.sort(reverse=True)
    selected=[]
    for _,x,z in result:
        if all(math.hypot(x-a,z-b)>=2 for a,b in selected):selected.append([x,z])
        if len(selected)>=8:break
    return selected


def compile_treasure(reference,payload,*,namespace='treasure-v1',classify=True):
    s,w,n,e=reference.bbox
    nodes={v['id']:(v['lat'],v['lon']) for v in payload['elements'] if v.get('type')=='node' and 'lat' in v}
    buildings=[];land=[];seen=set();pois=[]
    def project(lat,lon):return [round(v,3) for v in reference.project(lat,lon)]
    for item in payload['elements']:
        tags=item.get('tags',{})
        if classify and (tags.get('shop') not in (None,'no') or tags.get('amenity') in ('restaurant','cafe','fast_food','food_court','marketplace','bar','pub')):
            geometry=item.get('geometry',[])
            point=(item['lat'],item['lon']) if 'lat' in item else (sum(p['lat'] for p in geometry)/len(geometry),sum(p['lon'] for p in geometry)/len(geometry)) if geometry else None
            if point is None:
                coordinates=[nodes[i] for i in item.get('nodes',[]) if i in nodes]
                if coordinates:point=(sum(p[0] for p in coordinates)/len(coordinates),sum(p[1] for p in coordinates)/len(coordinates))
            if point:pois.append(project(*point))
        if item.get('type')!='way':continue
        tags=item.get('tags',{});geometry=item.get('geometry')
        points=[(p['lat'],p['lon']) for p in geometry if p and 'lat' in p] if geometry else [nodes[i] for i in item.get('nodes',[]) if i in nodes]
        if len(points)<4 or points[0]!=points[-1]:continue
        ring=[project(lat,lon) for lat,lon in points]
        if 'landuse' in tags:land.append((tags['landuse'],ring))
        if tags.get('natural') in ('wood','water','coastline'):land.append(('forest' if tags['natural']=='wood' else 'coast',ring))
        kind=tags.get('building')
        if kind is None or kind in EXCLUDED or tags.get('building:part') or tags.get('location')=='underground':continue
        cx=sum(p[0] for p in ring[:-1])/(len(ring)-1);cz=sum(p[1] for p in ring[:-1])/(len(ring)-1)
        if not reference.contains(cx,cz):continue
        signature=tuple(sorted(set(tuple(p) for p in ring)))
        if signature in seen:continue
        seen.add(signature)
        buildings.append({'id':'way:'+str(item['id']),'kind':kind,'ring':ring,'center':[cx,cz],'tags':tags})
    plans=[];eligible=0;radius=250*reference.scale
    # Query only nearby land polygons/buildings. A city-wide all-to-all scan
    # makes retrying the gameplay phase take longer than the world itself.
    land_grid={};large_land=[];building_grid={};poi_grid={}
    if classify:
        for use,ring in land:
            box=(min(p[0] for p in ring),min(p[1] for p in ring),max(p[0] for p in ring),max(p[1] for p in ring))
            x0,z0,x1,z1=[math.floor(v/radius) for v in box];entry=(use,ring,box)
            if (x1-x0+1)*(z1-z0+1)>2048:large_land.append(entry);continue
            for gx in range(x0,x1+1):
                for gz in range(z0,z1+1):land_grid.setdefault((gx,gz),[]).append(entry)
        for building in buildings:
            x,z=building['center'];building_grid.setdefault((math.floor(x/radius),math.floor(z/radius)),[]).append((x,z))
        for x,z in pois:poi_grid.setdefault((math.floor(x/radius),math.floor(z/radius)),[]).append((x,z))
    for building in buildings:
        spots=[p for p in candidates(building['ring']) if reference.contains(*p)]
        if not spots:continue
        eligible+=1
        digest=hashlib.sha256((reference.map_id+'/'+namespace+'/'+building['id']).encode()).digest()
        if not reference.treasure_enabled or int.from_bytes(digest[:4],'big')/2**32>=reference.treasure_probability:continue
        ring=building['ring']
        if not classify:
            plans.append({'id':digest[:12].hex(),'source':building['id'],'points':spots,
                          'bounds':[math.floor(min(p[0] for p in ring)),math.floor(min(p[1] for p in ring)),
                                    math.ceil(max(p[0] for p in ring)),math.ceil(max(p[1] for p in ring))],
                          'seed':int.from_bytes(digest[12:16],'big') or 1})
            continue
        cx,cz=building['center'];scores={'farm':0,'urban':0,'industrial':0,'retail':0,'forest':0,'coast':0}
        for dx in (-1,-.5,0,.5,1):
            for dz in (-1,-.5,0,.5,1):
                x,z=cx+dx*radius,cz+dz*radius
                for use,ring,box in land_grid.get((math.floor(x/radius),math.floor(z/radius)),[])+large_land:
                    if box[0]<=x<=box[2] and box[1]<=z<=box[3] and inside(x,z,ring):
                        if use in FARMS:scores['farm']+=1
                        elif use in URBAN:
                            scores['urban']+=1
                            if use=='retail':scores['retail']+=1
                        elif use=='industrial':scores['industrial']+=1
                        elif use in ('forest','coast'):scores[use]+=1
        gx,gz=math.floor(cx/radius),math.floor(cz/radius)
        density=sum(math.hypot(cx-x,cz-z)<=radius for dx in (-1,0,1) for dz in (-1,0,1) for x,z in building_grid.get((gx+dx,gz+dz),[]))
        if building['kind'] in ('industrial','warehouse') or scores['industrial']>=max(3,scores['farm'],scores['urban']):theme='industrial'
        elif building['kind'] in ('farm','barn') or scores['farm']>=max(3,scores['urban']):theme='farm'
        elif building['kind'] in ('office','commercial','retail') or density>=40 or scores['urban']>=8:theme='urban'
        else:theme='mixed'
        shops=sum(math.hypot(cx-x,cz-z)<=120*reference.scale for dx in (-1,0,1) for dz in (-1,0,1) for x,z in poi_grid.get((gx+dx,gz+dz),[]))
        if building['kind']=='retail' or building['tags'].get('shop') not in (None,'no') or scores['retail']>=3 or shops:theme='retail'
        elif theme=='mixed' and scores['forest']>=3:theme='forest'
        elif theme=='mixed' and scores['coast']>=3:theme='coast'
        ring=building['ring']
        plans.append({'id':digest[:12].hex(),'source':building['id'],'theme':theme,'points':spots,
                      'bounds':[math.floor(min(p[0] for p in ring)),math.floor(min(p[1] for p in ring)),
                                math.ceil(max(p[0] for p in ring)),math.ceil(max(p[1] for p in ring))],
                      'seed':int.from_bytes(digest[12:16],'big') or 1})
    return {'version':2,'enabled':reference.treasure_enabled,'probability':reference.treasure_probability,'eligible':eligible,'selected':len(plans),
            'source':'OpenStreetMap','plans':plans}


def prepare_treasure(reference,root):
    root=Path(root)
    from jobcache import plan_key,load_plan,atomic_json
    key=plan_key(reference,root/'osm.json','treasure');path=root/'treasure-plan.json'
    cached=load_plan(path,key)
    if cached is not None:return cached
    data=compile_treasure(reference,json.loads((root/'osm.json').read_text(encoding='utf-8')))
    data['cache_key']=key;atomic_json(path,data)
    return data
