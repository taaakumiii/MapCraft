"""Independent deterministic building selection for indoor two-block beds."""
from dataclasses import replace
import json
from pathlib import Path
from treasure import compile_treasure

def compile_beds(reference,payload):
    options=replace(reference,treasure_enabled=reference.beds_enabled,
                    treasure_probability=reference.beds_probability)
    return compile_treasure(options,payload,namespace='beds-v1',classify=False)

def prepare_beds(reference,root):
    root=Path(root)
    from jobcache import plan_key,load_plan,atomic_json
    key=plan_key(reference,root/'osm.json','beds');path=root/'bed-plan.json'
    cached=load_plan(path,key)
    if cached is not None:return cached
    data=compile_beds(reference,json.loads((root/'osm.json').read_text(encoding='utf-8')))
    data['cache_key']=key;atomic_json(path,data)
    return data
