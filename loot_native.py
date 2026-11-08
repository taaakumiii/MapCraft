"""Deterministic creator-side equivalent of loot.js, for ordinary chest NBT."""
from pathlib import Path
import json
import re

PROFILES={
 'retail':dict(food=60,farm=5,equipment=10,industrial=3,building=12,forest=3,coast=4,alchemy=2,treasure=1),
 'farm':dict(food=25,farm=40,equipment=10,industrial=4,building=8,forest=8,coast=2,alchemy=2,treasure=1),
 'industrial':dict(food=8,farm=2,equipment=26,industrial=40,building=15,forest=2,coast=2,alchemy=3,treasure=2),
 'urban':dict(food=22,farm=3,equipment=22,industrial=10,building=25,forest=3,coast=3,alchemy=6,treasure=6),
 'forest':dict(food=15,farm=12,equipment=12,industrial=4,building=10,forest=40,coast=2,alchemy=3,treasure=2),
 'coast':dict(food=22,farm=3,equipment=15,industrial=7,building=10,forest=4,coast=35,alchemy=2,treasure=2),
 'mixed':dict(food=28,farm=10,equipment=20,industrial=10,building=18,forest=6,coast=3,alchemy=3,treasure=2)}

VOLCANIC_MINERALS={'coal','charcoal','raw_iron','iron_ingot','raw_copper','copper_ingot','raw_gold','gold_ingot','gold_nugget','iron_nugget','redstone','lapis_lazuli','amethyst_shard','quartz','flint'}

def random(seed):
    value=seed&0xffffffff
    def next_value():
        nonlocal value
        value=(value*1664525+1013904223)&0xffffffff
        return value/4294967296
    return next_value

def loot_for(plan,pool=None):
    if pool is None:pool=json.loads((Path(__file__).parent/'survival-items.json').read_text(encoding='utf-8'))['items']
    r=random(plan['seed']^0x7a31b029);count=3+int(r()*4);gate=r()
    special='relic' if gate<.0002 else 'rare' if gate<.008 else None
    used=set();items=[];weights=PROFILES.get(plan.get('theme'),PROFILES['mixed'])
    def choose(tier):
        groups={}
        for entry in pool:
            if entry['tier']==tier and entry['id'] not in used:groups.setdefault(entry['family'],[]).append(entry)
        position=r()*sum(weights.get(family,1) for family in groups)
        for family,entries in groups.items():
            position-=weights.get(family,1)
            if position<0:return entries[int(r()*len(entries))]
        return None
    for i in range(count):
        tier=special if i==0 and special else 'uncommon' if r()<.12 else 'common'
        entry=None
        if plan.get('volcano') and tier in ('common','uncommon') and r()<.5*plan['volcano']['strength']/1000:
            minerals=[e for e in pool if e['tier']==tier and e['id'] in VOLCANIC_MINERALS and e['id'] not in used]
            if minerals:entry=minerals[int(r()*len(minerals))]
        entry=entry or choose(tier) or choose('common')
        if entry is None:continue
        maximum=min(entry['max'],3) if plan.get('volcano') and entry['id'] in VOLCANIC_MINERALS else entry['max']
        used.add(entry['id']);items.append({'type':'minecraft:'+entry['id'],'amount':1+int(r()*maximum),
                                          'tier':entry['tier'],'seed':int(r()*4294967296)})
    slots=list(range(27))
    for item in items:item['slot']=slots.pop(int(r()*len(slots)))
    return items

def item_nbt(item):
    import amulet_nbt as nbt
    name=item['type'];short=name.split(':')[-1];r=random(item.get('seed',1));damage=0;tag={}
    maximum=1 if re.search(r'(?:bucket|_boat|_raft|minecart|_stew|_soup|_potion|^potion|_book|bundle|_armor|_banner_pattern|_harness)$',short) or short in ('enchanted_book','bed','cake','goat_horn','carrot_on_a_stick') else 64
    if re.search(r'_(?:axe|hoe|pickaxe|shovel|sword|spear|boots|helmet|chestplate|leggings|horse_armor|nautilus_armor)$',short):maximum=1
    if short=='enchanted_book':
        tag['ench']=nbt.ListTag([nbt.CompoundTag({'id':nbt.ShortTag(int(r()*42)),'lvl':nbt.ShortTag(1)})])
    if short in ('potion','splash_potion','lingering_potion'):
        effects=[v for v in range(47) if v not in (2,36)]
        damage=effects[int(r()*len(effects))];maximum=1
    fields={'Name':nbt.StringTag(name),'Count':nbt.ByteTag(min(item['amount'],maximum)),
            'Damage':nbt.ShortTag(damage),'Slot':nbt.ByteTag(item['slot']),'WasPickedUp':nbt.ByteTag(0)}
    if tag:fields['tag']=nbt.CompoundTag(tag)
    return nbt.CompoundTag(fields)
