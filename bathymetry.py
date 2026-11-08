"""Cached NOAA ETOPO 2022 numeric bathymetry, not a rendered relief image."""
from functools import lru_cache
import io
import json
import math
import os
import time
from pathlib import Path
import urllib.parse
import urllib.request

DEFAULT={'enabled':True,'max_depth':60}
CREDITS='NOAA National Centers for Environmental Information (2022): ETOPO 2022 15 Arc-Second Global Relief Model. DOI: 10.25921/fd45-gt74. https://www.ncei.noaa.gov/products/etopo-global-relief-model\n元データは15秒（日本付近で約400〜500m）間隔。補間したゲーム用の近似であり、浅海の小さな岩礁や港湾の最新形状を保証しません。ワールドの高さ制限に合わせ深い海は上限で切り詰めます。\n'

def options(value=None):
    result={**DEFAULT,**(value or {})}
    if not isinstance(result['enabled'],bool) or isinstance(result['max_depth'],bool) or not isinstance(result['max_depth'],int) or not 2<=result['max_depth']<=60:
        raise ValueError('海底補正の深さ上限は2〜60ブロックです。')
    return result

class Survey:
    def __init__(self,cache,notify=lambda _:None):self.cache=Path(cache)/'etopo2022-15s';self.notify=notify;self.cache.mkdir(parents=True,exist_ok=True)
    @lru_cache(maxsize=16)
    def tile(self,tx,ty):
        import numpy as np
        from PIL import Image
        west=tx-180;north=90-ty;path=self.cache/f'{west}_{north}.tif'
        if path.exists():raw=path.read_bytes()
        else:
            query={'bbox':f'{west},{north-1},{west+1},{north}','bboxSR':4326,'size':'240,240','imageSR':4326,
                'format':'tiff','pixelType':'F32','interpolation':'RSP_NearestNeighbor','compression':'LZ77',
                'renderingRule':json.dumps({'rasterFunction':'none'}),'mosaicRule':json.dumps({'where':"Name='ETOPO_2022_v1_15s_bed_elev'"}),'f':'image'}
            url='https://gis.ngdc.noaa.gov/arcgis/rest/services/DEM_mosaics/DEM_all/ImageServer/exportImage?'+urllib.parse.urlencode(query)
            self.notify(f'海底標高データを取得: {west}〜{west+1}°E / {north-1}〜{north}°N')
            request=urllib.request.Request(url,headers={'User-Agent':'MapCraft (ETOPO bathymetry)'})
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(request,timeout=60) as response:raw=response.read(2*1024*1024)
                    break
                except (OSError,TimeoutError):
                    if attempt==2:raise
                    time.sleep(.5*2**attempt)
        with Image.open(io.BytesIO(raw)) as image:
            if image.mode!='F' or image.size!=(240,240):raise ValueError('海底データが数値標高TIFFではありません。')
            values=np.array(image,dtype=np.float32)
        valid=np.isfinite(values)&(abs(values)<20000)
        if not valid.any():raise ValueError('海底データに利用可能な標高がありません。')
        values[~valid]=np.nan
        if not path.exists():
            temporary=path.with_suffix('.'+str(os.getpid())+'.tmp');temporary.write_bytes(raw);os.replace(temporary,path)
        return values
    def elevation(self,lat,lon):
        x=(lon+180)*240-.5;y=(90-lat)*240-.5;ix=math.floor(x);iy=math.floor(y);fx=x-ix;fy=y-iy
        total=weight=0.
        for dx,dy,w in ((0,0,(1-fx)*(1-fy)),(1,0,fx*(1-fy)),(0,1,(1-fx)*fy),(1,1,fx*fy)):
            xx=(ix+dx)%86400;yy=max(0,min(43199,iy+dy));v=float(self.tile(xx//240,yy//240)[yy%240,xx%240])
            if math.isfinite(v):total+=v*w;weight+=w
        return total/weight if weight>.999 else None
