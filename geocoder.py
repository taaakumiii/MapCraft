"""Explicit Japanese place searches through Photon, with caching and throttling."""
import hashlib
import json
import math
import re
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from core import square, validate_bbox, dimensions

USER_AGENT = 'MapCraft/0.2 (local Minecraft world generator; desktop place search)'
DEFAULT_ENDPOINT = 'https://photon.komoot.io/api/'

def search_terms(query):
    """Separate Japanese administrative names for Photon's multilingual index."""
    query=' '.join(query.strip().split())
    match=re.match(r'^(北海道|東京都|京都府|大阪府|.{2,3}県)',query)
    if match:
        prefecture=match.group(0);rest=query[match.end():].strip()
        rest=re.sub(r'(.+?[市区郡町村])(?=[^\s市区郡町村])',r'\1 ',rest)
        query=prefecture+(' '+rest if rest else '')
    return ' '.join(query.split())

def normalize_results(payload):
    results=[]; seen=set()
    if not isinstance(payload,dict) or not isinstance(payload.get('features'),list):
        raise ValueError('検索サービスから正しい検索結果を取得できませんでした。')
    for feature in payload['features']:
        try:
            props=feature['properties']; geometry=feature['geometry']
            if geometry.get('type')!='Point' or props.get('countrycode','').upper()!='JP':continue
            lon,lat=map(float,geometry['coordinates'][:2])
            if not (math.isfinite(lat) and math.isfinite(lon) and 20<=lat<=46.5 and 122<=lon<=154.5):continue
            name=str(props.get('name') or props.get('street') or '検索地点')
            identity=(name,round(lat,6),round(lon,6))
            if identity in seen:continue
            seen.add(identity)
            parts=[]
            for key in ('state','county','city','district','locality','street'):
                part=props.get(key)
                if part and part!=name and part not in parts:parts.append(str(part))
            label=name+(' ｜ '+' '.join(parts) if parts else '')
            extent=props.get('extent'); bbox=None
            if extent and len(extent)==4:
                w,n,e,s=map(float,extent)
                try:bbox=validate_bbox((s,w,n,e))
                except ValueError:pass
            # Very small POIs are easier to select with their surroundings in view.
            if bbox:
                width,height,*_=dimensions(bbox)
                if width<500 or height<500:
                    pad=square(lat,lon,500);bbox=(min(bbox[0],pad[0]),min(bbox[1],pad[1]),max(bbox[2],pad[2]),max(bbox[3],pad[3]))
            if bbox is None:bbox=square(lat,lon,1000)
            results.append({'name':name,'label':label,'lat':lat,'lon':lon,'bbox':bbox,
                            'has_extent':bool(extent),'osm_type':props.get('osm_type'),'osm_id':props.get('osm_id')})
        except (KeyError,TypeError,ValueError,OverflowError):continue
    return results

class Geocoder:
    def __init__(self,base):
        self.base=Path(base);self.lock=threading.Lock();self.last_request=0
        self.endpoint=DEFAULT_ENDPOINT
        config=self.base/'settings.json'
        if config.exists():
            self.endpoint=json.loads(config.read_text(encoding='utf-8')).get('search_endpoint',DEFAULT_ENDPOINT)
        if not self.endpoint.startswith('https://'):
            raise ValueError('検索サービスURLはhttps://で始まる必要があります。')

    def search(self,query):
        query=search_terms(query)
        if not query:raise ValueError('地名・駅名・住所を入力してください。')
        if len(query)>200:raise ValueError('検索文字列は200文字以内にしてください。')
        key=hashlib.sha256((self.endpoint+'\n'+query).encode()).hexdigest()
        cache=self.base/'cache/search'/(key+'.json')
        with self.lock:
            if cache.exists() and time.time()-cache.stat().st_mtime<30*86400:
                try:return normalize_results(json.loads(cache.read_text(encoding='utf-8')))
                except (ValueError,OSError):pass
            wait=1.1-(time.monotonic()-self.last_request)
            if wait>0:time.sleep(wait)
            url=self.endpoint+('?' if '?' not in self.endpoint else '&')+urllib.parse.urlencode({'q':query,'countrycode':'JP','limit':10})
            self.last_request=time.monotonic()
            req=urllib.request.Request(url,headers={'User-Agent':USER_AGENT,'Accept':'application/json'})
            try:
                with urllib.request.urlopen(req,timeout=20) as response:payload=json.load(response)
            except urllib.error.HTTPError as e:
                if e.code==429:raise RuntimeError('検索が混み合っています。少し時間をおいて検索してください。') from e
                raise RuntimeError(f'検索サービスに接続できませんでした（HTTP {e.code}）。') from e
            except (OSError,ValueError) as e:
                raise RuntimeError('地名検索に接続できませんでした。通信状況を確認して再検索してください。') from e
            results=normalize_results(payload)
            try:
                cache.parent.mkdir(parents=True,exist_ok=True);cache.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
            except OSError:pass  # A read-only cache must not prevent searching.
            return results
