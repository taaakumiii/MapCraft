"""Render offline cartography from real OSM vectors; never archive public OSM raster tiles."""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sys
import urllib.parse
import urllib.request
from PIL import Image, ImageDraw, ImageFont
from core import pixel
from map_water import LAND_COLOUR,WATER_COLOUR,RENDER_STYLE,water_areas,ocean_area,paint_water

CELL=32
FRAME_SIZE=(54,74)

def frame_bytes():
    stream=io.BytesIO()
    Image.new('RGBA',FRAME_SIZE,(24,34,43,210)).save(stream,format='PNG')
    return stream.getvalue()

def code(number):
    return ''.join(chr(97+((number>>shift)&15)) for shift in (12,8,4,0))

def cache_root():
    base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
    return base/'real_maps'

def fetch_osm(reference,path):
    s,w,n,e=reference.bbox
    box=','.join(str(v) for v in (s-.004,w-.005,n+.004,e+.005))
    filters=['way[highway]','way[building]','way[waterway]','way[natural=water]','way[natural=coastline]',
             'relation[type=multipolygon][natural=water]','relation[type=multipolygon][waterway=riverbank]',
             'relation[type=multipolygon][landuse=reservoir]',
             'way[landuse]','way[leisure=park]','way[railway]',
             'node[name][place]','node[name][railway=station]','node[name][amenity]']
    query='[out:json][timeout:60];('+''.join(f'{f}({box});' for f in filters)+');out geom;'
    last=None
    for endpoint in ('https://overpass-api.de/api/interpreter','https://overpass.kumi.systems/api/interpreter'):
        try:
            request=urllib.request.Request(endpoint,data=urllib.parse.urlencode({'data':query}).encode(),headers={'User-Agent':'MapCraft/0.5 (offline cartography from OpenStreetMap vectors)'})
            with urllib.request.urlopen(request,timeout=75) as response:data=response.read()
            obj=json.loads(data)
            if not isinstance(obj.get('elements'),list):raise ValueError('地図データを取得できませんでした。')
            obj['mapcraft_water_query']=RENDER_STYLE
            path.write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':')),encoding='utf-8');return
        except Exception as error:last=error
    raise RuntimeError('現実の地図データを取得できませんでした。通信状況を確認して再実行してください。 '+str(last))

def font(size):
    for name in ('YuGothR.ttc','meiryo.ttc','msgothic.ttc'):
        path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name
        if path.exists():return ImageFont.truetype(str(path),size)
    return ImageFont.load_default(size=size)

def prepare_map(reference,source=None,root=None,zooms=None):
    from jobcache import atomic_json,source_hash
    root=Path(root) if root else cache_root()/reference.map_id
    root.mkdir(parents=True,exist_ok=True);datafile=root/'osm.json';meta=root/'atlas.json'
    render_key={'style':RENDER_STYLE,'bbox':list(reference.bbox),'zooms':list(zooms) if zooms else [14,15,16]}
    if meta.exists() and (root/'cells').exists() and (root/'CREDITS.txt').exists() and datafile.exists():
        try:
            atlas=json.loads(meta.read_text(encoding='utf-8'))
            expected={'c'+code(i)+'.png' for i in range(atlas['cells'])}|{'czzzz.png'}
            if atlas.get('render_key')==render_key and expected<={p.name for p in (root/'cells').glob('*.png')} and source_hash(datafile)==atlas.get('sha256'):return root,atlas
        except (OSError,ValueError,KeyError,TypeError):pass
    if not datafile.exists():
        if source and Path(source).exists():
            temporary=datafile.with_suffix('.copying');shutil.copyfile(source,temporary);os.replace(temporary,datafile)
        else:fetch_osm(reference,datafile)
    payload=json.loads(datafile.read_text(encoding='utf-8'))
    if source is None and str(payload.get('generator','')).startswith('Overpass') and payload.get('mapcraft_water_query')!=RENDER_STYLE:
        # Old downloaded maps omitted coastlines and lake relations. Local PBF
        # extracts and explicit caller datasets never require a network request.
        fetch_osm(reference,datafile);payload=json.loads(datafile.read_text(encoding='utf-8'))
    elements=payload['elements']
    nodes={v['id']:(v['lat'],v['lon']) for v in elements if v.get('type')=='node' and 'lat' in v}
    features=[]
    for item in elements:
        tags=item.get('tags',{})
        if not tags:continue
        geometry=item.get('geometry')
        points=[(p['lat'],p['lon']) for p in geometry if p and 'lat' in p] if geometry else [nodes[i] for i in item.get('nodes',[]) if i in nodes]
        if item.get('type')=='node' and 'lat' in item:points=[(item['lat'],item['lon'])]
        if points:features.append((tags,points))
    s,w,n,e=reference.bbox
    zooms=list(zooms) if zooms else [14,15,16]
    def bounds(z):
        x1,y1=pixel(n,w,z);x2,y2=pixel(s,e,z)
        return math.floor(x1/CELL)-3,math.floor(y1/CELL)-3,math.ceil(x2/CELL)+3,math.ceil(y2/CELL)+3
    while sum((bounds(z)[2]-bounds(z)[0])*(bounds(z)[3]-bounds(z)[1]) for z in zooms)>60000:
        zooms.pop()
    if not zooms:raise ValueError('現実地図の範囲が大きすぎます。範囲を小さくしてください。')
    cells=root/'cells';cells.mkdir(exist_ok=True)
    levels=[];next_id=0
    for z in zooms:
        gx,gy,right,bottom=bounds(z);width,height=(right-gx)*CELL,(bottom-gy)*CELL
        image=Image.new('RGB',(width,height),LAND_COLOUR);draw=ImageDraw.Draw(image)
        def project(lat,lon):
            x,y=pixel(lat,lon,z)
            return x-gx*CELL,y-gy*CELL
        prepared=[]
        for tags,points in features:
            coords=[(round(pixel(lat,lon,z)[0]-gx*CELL),round(pixel(lat,lon,z)[1]-gy*CELL)) for lat,lon in points]
            prepared.append((tags,coords))
        # Ground use, water, then footprints. All coordinates are actual geographic data.
        for tags,points in prepared:
            if len(points)<3 or points[0]!=points[-1]:continue
            colour=None
            use=tags.get('landuse')
            if use in ('forest','wood'):colour='#d4e3c7'
            elif use in ('farmland','farmyard','orchard','meadow','grass'):colour='#e6ead4'
            elif tags.get('leisure')=='park':colour='#cce4bc'
            if colour:draw.polygon(points,fill=colour)
        coasts=[[project(lat,lon) for lat,lon in points] for tags,points in features if tags.get('natural')=='coastline' and len(points)>1]
        areas=water_areas(elements,nodes,project)
        areas.append(ocean_area(coasts,(0,0,width,height)))
        paint_water(image,areas)
        for tags,points in prepared:
            if len(points)>2 and tags.get('building') not in (None,'no'):
                draw.polygon(points,fill='#e0dbd0',outline='#bfb8ad')
        for tags,points in prepared:
            if len(points)>1 and tags.get('waterway') in ('river','stream','canal','drain','ditch'):
                draw.line(points,fill=WATER_COLOUR,width=5 if tags['waterway']=='river' else 2,joint='curve')
        roads=[(tags,points) for tags,points in prepared if 'highway' in tags and len(points)>1]
        widths={'motorway':7,'trunk':6,'primary':5,'secondary':5,'tertiary':4,'residential':3,'unclassified':3,'service':2}
        for tags,points in roads:
            kind=tags['highway'];width_px=widths.get(kind,2)
            draw.line(points,fill='#c7c4bb',width=width_px+2,joint='curve')
        for tags,points in roads:
            kind=tags['highway'];colour='#efb4a7' if kind=='motorway' else '#f3d98e' if kind in ('trunk','primary','secondary') else '#ffffff'
            draw.line(points,fill=colour,width=widths.get(kind,2),joint='curve')
        for tags,points in prepared:
            if len(points)>1 and tags.get('railway') in ('rail','light_rail','tram'):
                draw.line(points,fill='#747b80',width=2)
        # Labels have a white halo and collision checks, so they remain readable in the game.
        occupied=[];fonts={n:font(n) for n in (10,12,14,16)};labels=[]
        for tags,points in prepared:
            name=tags.get('name:ja') or tags.get('name')
            if not name or len(name)>30:continue
            priority=0;size=12;colour='#3d4349'
            if tags.get('railway')=='station':priority=5;size=14;colour='#1d5873'
            elif 'place' in tags:priority=4;size=16
            elif tags.get('amenity') in ('school','townhall','hospital','library','post_office'):priority=3
            elif 'highway' in tags and tags['highway'] in ('primary','secondary','tertiary'):priority=2;size=10
            elif 'building' in tags:priority=1;size=10
            if priority:
                point=points[len(points)//2] if 'highway' in tags else (sum(p[0] for p in points)/len(points),sum(p[1] for p in points)/len(points))
                labels.append((priority,name,point,size,colour))
        for _,name,(x,y),size,colour in sorted(labels,key=lambda v:-v[0]):
            f=fonts[size];box=draw.textbbox((x,y),name,font=f,anchor='mm',stroke_width=1)
            if box[2]<0 or box[3]<0 or box[0]>width or box[1]>height:continue
            if any(box[0]<b[2]+5 and box[2]>b[0]-5 and box[1]<b[3]+3 and box[3]>b[1]-3 for b in occupied):continue
            occupied.append(box);draw.text((x,y),name,font=f,fill=colour,anchor='mm',stroke_width=2,stroke_fill='#fffffc')
        image.save(root/f'map_z{z}.png')
        cols,rows=right-gx,bottom-gy;offset=next_id
        for row in range(rows):
            for col in range(cols):
                image.crop((col*CELL,row*CELL,(col+1)*CELL,(row+1)*CELL)).save(cells/('c'+code(next_id)+'.png'))
                next_id+=1
        levels.append({'zoom':z,'gx':gx,'gy':gy,'cols':cols,'rows':rows,'offset':offset})
    Image.new('RGB',(CELL,CELL),'#d8dce0').save(cells/'czzzz.png')
    result={'levels':levels,'cell':CELL,'cells':next_id,'source':'OpenStreetMap','license':'ODbL-1.0',
            'copyright':'https://www.openstreetmap.org/copyright','sha256':source_hash(datafile),'render_key':render_key}
    (root/'CREDITS.txt').write_text('Map data © OpenStreetMap contributors, ODbL 1.0.\nhttps://www.openstreetmap.org/copyright\nhttps://opendatacommons.org/licenses/odbl/1-0/\nCartography rendered by MapCraft from the included OSM vector dataset.\n',encoding='utf-8')
    atomic_json(meta,result)
    return root,result

def slice_text(start,end):
    return f"(('%.{end}s' * #preserved_text) - ('%.{start}s' * #preserved_text))"

def hud_document(digits=4):
    def bind(expr,target):
        return {'binding_type':'view','source_control_name':'realmap_data','resolve_sibling_scope':True,'source_property_name':expr,'target_property_name':target}
    data={'type':'panel','size':[0,0],'property_bag':{'#preserved_text':''},'bindings':[
        {'binding_name':'#hud_title_text_string'},
        {'binding_name':'#hud_title_text_string','binding_name_override':'#preserved_text','binding_condition':'visibility_changed'},
        {'binding_type':'view','source_property_name':"(not (#hud_title_text_string = #preserved_text) and not ((#hud_title_text_string - 'mc:') = #hud_title_text_string))",'target_property_name':'#visible'}]}
    controls=[{'realmap_data':data}]
    controls.append({'realmap_frame':{'type':'image','texture':'textures/ui/mapcraft_real/frame','size':list(FRAME_SIZE),'keep_ratio':False,
        'anchor_from':'top_right','anchor_to':'top_right','offset':[0,0],'layer':0,
        'bindings':[bind(f"(({slice_text(0,7)}) = 'mc:real')",'#visible')]}})
    # Header: marker7, heading1, arrow-position2, GPS28, block-coordinates28, cells25*4.
    for row in range(5):
        for col in range(5):
            start=66+(row*5+col)*digits
            controls.append({f'realmap_cell_{row}_{col}':{'type':'image','size':[10,10],
                'anchor_from':'top_right','anchor_to':'top_right','offset':[-2-(4-col)*10,2+row*10],
                'texture':'textures/ui/mapcraft_real/czzzz','layer':1,
                'bindings':[bind(f"('textures/ui/mapcraft_real/c' + {slice_text(start,start+digits)})",'#texture')]}})
    for row in range(16):
        for col in range(16):
            position=chr(97+col)+chr(97+row)
            x=-54+2+(64+col*2+1)/32*10;y=2+(64+row*2+1)/32*10
            controls.append({f'realmap_pointer_{row}_{col}':{'type':'image','size':[5,5],
                'anchor_from':'top_right','anchor_to':'center','offset':[x,y],'layer':3,
                'texture':'textures/ui/minimap/arrow0',
                'bindings':[bind(f"('textures/ui/minimap/arrow' + {slice_text(7,8)})",'#texture'),
                            bind(f"({slice_text(8,10)} = '{position}')",'#visible')]}})
    for i,start in enumerate((10,38)):
        controls.append({f'realmap_info_{i}':{'type':'label','size':[100,5],'anchor_from':'top_right','anchor_to':'top_right',
            'offset':[-1,54+i*5],'text':'#text','text_alignment':'right','font_scale_factor':.5,'shadow':True,'layer':3,
            'bindings':[bind(slice_text(start,start+28),'#text')]}})
    for i,line in enumerate(('© OpenStreetMap','contributors / ODbL')):
        controls.append({f'realmap_credit_{i}':{'type':'label','size':[60,4],'anchor_from':'top_right','anchor_to':'top_right',
            'offset':[-1,65+i*3.5],'text':line,'text_alignment':'right','font_scale_factor':.3,'shadow':True,'layer':3}})
    panel={'type':'panel','size':[54,74],'anchor_from':'top_right','anchor_to':'top_right','offset':[-4,4],'controls':controls}
    for item in controls:
        name,control=next(iter(item.items()))
        if name!='realmap_data' and not name.startswith('realmap_pointer_'):
            control.setdefault('bindings',[]).append(bind(f"({slice_text(0,7)} = 'mc:real')",'#visible'))
    return {'namespace':'hud','root_panel':{'modifications':[{'array_name':'controls','operation':'insert_back','value':{'mapcraft_real@hud.mapcraft_real':{}}}]},
        'hud_title_text':{'modifications':[{'array_name':'bindings','operation':'insert_back','value':[
            {'binding_name':'#hud_title_text_string','binding_type':'global'},
            {'binding_type':'view','source_property_name':"((#hud_title_text_string - 'mc:') = #hud_title_text_string)",'target_property_name':'#visible'}]}]},
        'mapcraft_real':panel}
