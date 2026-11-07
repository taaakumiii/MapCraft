import {world,system} from '@minecraft/server';
import {registerTreasure} from './treasure.js';
import {registerBeds} from './beds.js';
import {regionTiles,tileSize} from './region-index.js';
const available=new Set(regionTiles),loaded=new Set(),pending=new Set(),retry=new Map();
let loading=0;
// Keep world startup independent of the number of buildings across the map.
system.runInterval(()=>{
    if(loading>=2)return;
    for(const player of world.getAllPlayers()){
        if(player.dimension.id!=='minecraft:overworld')continue;
        const x=Math.floor(player.location.x/tileSize),z=Math.floor(player.location.z/tileSize);
        for(let dx=-1;dx<=1;dx++)for(let dz=-1;dz<=1;dz++){
            const key=`${x+dx}_${z+dz}`;
            if(loading>=2)return;
            if(!available.has(key)||loaded.has(key)||pending.has(key)||(retry.get(key)||0)>system.currentTick)continue;
            pending.add(key);loading++;
            import(`./regions/t${key}.js`).then(data=>{
                registerTreasure(data.treasure);registerBeds(data.beds);loaded.add(key);
            }).catch(error=>{retry.set(key,system.currentTick+200);console.warn('MapCraft region '+key+': '+error);})
              .finally(()=>{loading--;pending.delete(key);});
        }
    }
},40);
