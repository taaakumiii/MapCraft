import { ItemTypes, ItemStack, EnchantmentTypes, Potions } from '@minecraft/server';
import { lootCatalogue } from './loot-catalogue.js';

export function random(seed){let value=seed>>>0;return ()=>{value=(Math.imul(value,1664525)+1013904223)>>>0;return value/4294967296;};}
const profiles={
    retail:{food:60,farm:5,equipment:10,industrial:3,building:12,forest:3,coast:4,alchemy:2,treasure:1},
    farm:{food:25,farm:40,equipment:10,industrial:4,building:8,forest:8,coast:2,alchemy:2,treasure:1},
    industrial:{food:8,farm:2,equipment:26,industrial:40,building:15,forest:2,coast:2,alchemy:3,treasure:2},
    urban:{food:22,farm:3,equipment:22,industrial:10,building:25,forest:3,coast:3,alchemy:6,treasure:6},
    forest:{food:15,farm:12,equipment:12,industrial:4,building:10,forest:40,coast:2,alchemy:3,treasure:2},
    coast:{food:22,farm:3,equipment:15,industrial:7,building:10,forest:4,coast:35,alchemy:2,treasure:2},
    mixed:{food:28,farm:10,equipment:20,industrial:10,building:18,forest:6,coast:3,alchemy:3,treasure:2}
};
let available;
const volcanicMinerals=new Set(['coal','charcoal','raw_iron','iron_ingot','raw_copper','copper_ingot','raw_gold','gold_ingot','gold_nugget','iron_nugget','redstone','lapis_lazuli','amethyst_shard','quartz','flint']);
function catalogue(){
    if(!available){
        const registry=new Set(ItemTypes.getAll().map(v=>v.id));
        available=lootCatalogue.filter(v=>registry.has('minecraft:'+v.id));
    }
    return available;
}
export function choose(tier,theme,r,used=new Set(),pool=catalogue()){
    const candidates=pool.filter(v=>v.tier===tier&&!used.has(v.id));
    const groups=new Map();
    for(const item of candidates){if(!groups.has(item.family))groups.set(item.family,[]);groups.get(item.family).push(item);}
    const weights=profiles[theme]||profiles.mixed;
    let position=r()*[...groups.keys()].reduce((n,key)=>n+(weights[key]||1),0);
    for(const [family,items] of groups){position-=weights[family]||1;if(position<0)return items[Math.floor(r()*items.length)];}
    return candidates[candidates.length-1];
}
export function lootFor(plan,pool=catalogue()){
    const r=random((plan.seed^0x7a31b029)>>>0),items=[],used=new Set();
    const count=3+Math.floor(r()*4);
    // One gate per chest, rather than per stack. Relics share the same one-slot
    // ceiling with rare loot. Dense resource blocks/ore/gear are also gated.
    const gate=r(),special=gate<.0002?'relic':gate<.008?'rare':undefined;
    for(let i=0;i<count;i++){
        const tier=i===0&&special?special:r()<.12?'uncommon':'common';
        let entry;
        if(plan.volcano&&['common','uncommon'].includes(tier)&&r()<.5*plan.volcano.strength/1000){
            const minerals=pool.filter(e=>e.tier===tier&&volcanicMinerals.has(e.id)&&!used.has(e.id));
            if(minerals.length)entry=minerals[Math.floor(r()*minerals.length)];
        }
        entry=entry||choose(tier,plan.theme,r,used,pool)||choose('common',plan.theme,r,used,pool);
        if(!entry)continue;
        used.add(entry.id);
        const maximum=plan.volcano&&volcanicMinerals.has(entry.id)?Math.min(entry.max,3):entry.max;
        items.push({type:'minecraft:'+entry.id,amount:1+Math.floor(r()*maximum),tier:entry.tier,seed:Math.floor(r()*4294967296)});
    }
    const slots=Array.from({length:27},(_,i)=>i);
    for(const item of items)item.slot=slots.splice(Math.floor(r()*slots.length),1)[0];
    return items;
}
export function stackFor(entry){
    const r=random(entry.seed||1),name=entry.type.split(':')[1];
    let stack;
    if(['potion','splash_potion','lingering_potion'].includes(name)){
        const effects=Potions.getAllEffectTypes().filter(v=>!/(?:decay|wither|luck|empty|long_mundane)/i.test(v.id));
        const delivery=name==='potion'?'Consume':name==='splash_potion'?'ThrownSplash':'ThrownLingering';
        if(effects.length)stack=Potions.resolve(effects[Math.floor(r()*effects.length)],delivery);
    }
    if(!stack)stack=new ItemStack(entry.type,1);
    stack.amount=Math.min(entry.amount,stack.maxAmount);
    if(name==='enchanted_book'){
        const component=stack.getComponent('minecraft:enchantable');
        const types=EnchantmentTypes.getAll().filter(type=>component?.canAddEnchantment({type,level:1}));
        if(types.length)component.addEnchantment({type:types[Math.floor(r()*types.length)],level:1});
    }
    return stack;
}
