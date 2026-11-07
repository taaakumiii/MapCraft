"""Bundle Japanese municipal boundaries from OSM for offline arrival detection."""
import json
from pathlib import Path
import urllib.parse
import urllib.request


def boundary_query(bbox):
    south,west,north,east=bbox
    # A relation's nodes can all lie outside a small selected world. Include
    # containing areas as well as boundary relations with nodes in the box.
    points=[(south,west),(south,east),(north,west),(north,east),
            ((south+north)/2,(west+east)/2)]
    query='[out:json][timeout:60];rel[boundary=administrative][admin_level=7]('
    query+=','.join(map(str,bbox))+')->.nearby;'
    for i,(lat,lon) in enumerate(points):query+=f'is_in({lat},{lon})->.p{i};'
    query+='('+''.join(f'.p{i};' for i in range(len(points)))+')->.areas;'
    return query+'area.areas[boundary=administrative][admin_level=7]->.municipal;(rel.nearby;rel(pivot.municipal););out geom;'


def join_rings(segments):
    pending=[list(segment) for segment in segments if len(segment)>1]
    rings=[]
    while pending:
        line=pending.pop()
        while line[0]!=line[-1]:
            for i,other in enumerate(pending):
                if line[-1]==other[0]:line.extend(other[1:])
                elif line[-1]==other[-1]:line.extend(reversed(other[:-1]))
                elif line[0]==other[-1]:line=other[:-1]+line
                elif line[0]==other[0]:line=list(reversed(other[1:]))+line
                else:continue
                pending.pop(i);break
            else:raise ValueError('市町村境界が閉じていません。地理データを再取得してください。')
        if len(line)>=4:rings.append(line)
    return rings


def compile_boundaries(payload):
    result=[]
    for relation in payload.get('elements',[]):
        tags=relation.get('tags',{})
        if relation.get('type')!='relation' or tags.get('boundary')!='administrative' or tags.get('admin_level')!='7':continue
        name=tags.get('name:ja') or tags.get('name')
        if not name:continue
        outer=[];inner=[]
        for member in relation.get('members',[]):
            if member.get('type')!='way' or member.get('role','') not in ('outer','inner',''):continue
            points=[(round(p['lon'],7),round(p['lat'],7)) for p in member.get('geometry',[]) if p and 'lat' in p]
            (inner if member.get('role')=='inner' else outer).append(points)
        outlines=join_rings(outer);holes=join_rings(inner)
        if not outlines:raise ValueError(f'{name}の市町村境界を取得できませんでした。')
        points=[p for ring in outlines for p in ring]
        result.append({'id':'osm:relation:'+str(relation['id']),'name':name,
                       'bounds':[min(p[0] for p in points),min(p[1] for p in points),
                                 max(p[0] for p in points),max(p[1] for p in points)],
                       'outer':outlines,'inner':holes})
    if not result:raise ValueError('この範囲の市町村境界が地図データに見つかりませんでした。')
    return {'version':1,'source':'OpenStreetMap','license':'ODbL-1.0','municipalities':result}


def prepare_boundaries(reference,root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    source=root/'boundaries-osm.json';compiled=root/'municipalities.json'
    if compiled.exists() and source.exists():return json.loads(compiled.read_text(encoding='utf-8'))
    if not source.exists():
        query=boundary_query(reference.bbox);last=None
        for endpoint in ('https://overpass-api.de/api/interpreter','https://overpass.kumi.systems/api/interpreter'):
            try:
                request=urllib.request.Request(endpoint,data=urllib.parse.urlencode({'data':query}).encode(),
                    headers={'User-Agent':'MapCraft/0.6 (offline municipal arrival notifications)'})
                with urllib.request.urlopen(request,timeout=75) as response:data=response.read()
                payload=json.loads(data);compile_boundaries(payload)
                source.write_bytes(data);break
            except Exception as error:last=error
        else:raise RuntimeError('到着通知用の市町村境界を取得できませんでした。通信状況を確認して再実行してください。 '+str(last))
    result=compile_boundaries(json.loads(source.read_text(encoding='utf-8')))
    compiled.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    return result
