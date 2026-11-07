import { world, system } from '@minecraft/server';
import { config, geographic, contains } from './geographic.js';
import { municipalities } from './municipalities.js';

const visits = new Map();
const property = 'mapcraft:last_municipality';

export function insideRing(lon, lat, ring) {
    let inside = false;
    for (let i=0,j=ring.length-1;i<ring.length;j=i++) {
        const [ax,ay]=ring[j], [bx,by]=ring[i];
        // Include exact boundary points; prefer the previously selected town.
        const cross=(lon-ax)*(by-ay)-(lat-ay)*(bx-ax);
        if (Math.abs(cross)<1e-12 && lon>=Math.min(ax,bx) && lon<=Math.max(ax,bx) &&
            lat>=Math.min(ay,by) && lat<=Math.max(ay,by)) return true;
        if ((ay>lat)!==(by>lat) && lon<(bx-ax)*(lat-ay)/(by-ay)+ax) inside=!inside;
    }
    return inside;
}

export function townAt(lon,lat,preferred='') {
    let found;
    for (const town of municipalities) {
        const [west,south,east,north]=town.bounds;
        if(lon<west || lon>east || lat<south || lat>north)continue;
        if(!town.outer.some(r=>insideRing(lon,lat,r)) || town.inner.some(r=>insideRing(lon,lat,r)))continue;
        if(town.id===preferred)return town;
        if(!found)found=town;
    }
    return found;
}

export function checkArrival(player,tick) {
    let visit=visits.get(player.id);
    if (!visit) {
        const saved=player.getDynamicProperty(property);
        visit={last:typeof saved==='string'?saved:'',candidate:'',since:tick};
        visits.set(player.id,visit);
    }
    const {x,z}=player.location;
    if (!config || player.dimension.id!=='minecraft:overworld' ||
        !contains(x,z)) {
        visit.candidate='';return;
    }
    const [lat,lon]=geographic(x,z);
    const town=townAt(lon,lat,visit.last);
    if (!town || town.id===visit.last) {visit.candidate='';return;}
    if (visit.candidate!==town.id) {visit.candidate=town.id;visit.since=tick;return;}
    if (tick-visit.since<40)return;
    player.setDynamicProperty(property,town.id);
    visit.last=town.id;visit.candidate='';
    // Raw text, rather than a command, safely displays names in multiplayer.
    world.sendMessage(`${player.name}が、${town.name}に到着`);
}

system.runInterval(()=>{
    for(const player of world.getAllPlayers()) {
        try {checkArrival(player,system.currentTick);}
        catch(error) {console.warn('MapCraft arrival: '+String(error));}
    }
},20);
world.afterEvents.playerLeave.subscribe(event=>visits.delete(event.playerId));
