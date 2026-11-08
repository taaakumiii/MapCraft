"""Atomic, input-keyed caches for expensive gameplay preparation."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

def atomic_json(path,data):
    path=Path(path);temporary=path.with_name(path.name+'.writing')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(data,stream,ensure_ascii=False,separators=(',',':'))
        stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)

def source_hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def plan_key(reference,source,kind):
    data=asdict(reference)
    common={k:data[k] for k in ('map_id','bbox','scale','span_x','span_z','projection','origin')}
    prefix='treasure' if kind=='treasure' else 'beds'
    return {'schema':3 if kind=='treasure' else 1,'kind':kind,'reference':common,'enabled':data[prefix+'_enabled'],
            'probability':data[prefix+'_probability'],'source_sha256':source_hash(source)}

def load_plan(path,key):
    try:
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data,dict):return None
        if data.get('cache_key')==json.loads(json.dumps(key)) and isinstance(data.get('plans'),list):return data
    except (OSError,ValueError):pass
    return None
