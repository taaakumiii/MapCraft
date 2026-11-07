import { world, system, CommandPermissionLevel, CustomCommandStatus } from '@minecraft/server';
import { ActionFormData } from '@minecraft/server-ui';
import { config, geographic } from './geographic.js';
import './arrival.js';
import './treasure.js';
import './beds.js';

const atlas = /*MAPCRAFT_ATLAS*/null;
const states = new Map();
const compass = ['N','NE','E','SE','S','SW','W','NW'];
function hex(number) {
    return [12,8,4,0].map(shift => String.fromCharCode(97+((number>>shift)&15))).join('');
}
function fixed(text) {return text.slice(0,28).padStart(28);}
function state(player) {
    if (!states.has(player.id)) {
        const level=Number(player.getDynamicProperty('mapcraft:real_zoom') ?? 1);
        states.set(player.id,{level:Math.max(0,Math.min(atlas.levels.length-1,level)),on:true,last:'',resent:0});
    }
    return states.get(player.id);
}
function update(player) {
    const s=state(player);
    if (!s.on) return;
    if (!config || player.dimension.id !== 'minecraft:overworld') {
        if(s.last!=='mc:hide')player.onScreenDisplay.setTitle('mc:hide',{fadeInDuration:0,stayDuration:1,fadeOutDuration:0});
        s.last='mc:hide';return;
    }
    const {x,z}=player.location;
    const [lat,lon]=geographic(x,z);
    const level=atlas.levels[s.level];
    const scale=256*2**level.zoom;
    const px=(lon+180)/360*scale;
    const py=(1-Math.asinh(Math.tan(lat*Math.PI/180))/Math.PI)/2*scale;
    const cx=Math.floor(px/32),cy=Math.floor(py/32);
    const qx=Math.min(15,Math.floor((px-cx*32)/2));
    const qy=Math.min(15,Math.floor((py-cy*32)/2));
    const yaw=player.getRotation().y;
    const arrow=((Math.round((yaw+180)/45)%8)+8)%8;
    let packet='mc:real'+arrow+String.fromCharCode(97+qx,97+qy);
    packet+=fixed(`N${lat.toFixed(5)} E${lon.toFixed(5)}`);
    packet+=fixed(`${compass[arrow]} X${Math.floor(x)} Z${Math.floor(z)}`);
    for (let row=0;row<5;row++) for(let col=0;col<5;col++) {
        const a=cx-2+col-level.gx,b=cy-2+row-level.gy;
        packet+=atlas.sparse ? (level.cells[`${cx-2+col},${cy-2+row}`] ?? 'zzzzzzzz') :
            a>=0 && b>=0 && a<level.cols && b<level.rows ? hex(level.offset+b*level.cols+a) : 'zzzz';
    }
    if (packet!==s.last || system.currentTick-s.resent>80) {
        player.onScreenDisplay.setTitle(packet,{fadeInDuration:0,stayDuration:1,fadeOutDuration:0});
        s.last=packet;s.resent=system.currentTick;
    }
}
system.runInterval(()=> {
    for (const player of world.getAllPlayers()) {
        try {update(player);} catch (_) { /* player may be leaving */ }
    }
},4);

function menu(player) {
    const s=state(player);
    new ActionFormData().title('MapCraft 現実の地図').body('OpenStreetMapの実在道路・川・建物・地名を表示します。')
        .button('拡大する').button('縮小する').button('標準の倍率に戻す')
        .show(player).then(r=>{
            if(r.canceled || r.selection===undefined)return;
            s.level=r.selection===0 ? Math.min(atlas.levels.length-1,s.level+1) : r.selection===1 ? Math.max(0,s.level-1) : Math.min(1,atlas.levels.length-1);
            player.setDynamicProperty('mapcraft:real_zoom',s.level);s.last='';update(player);
        });
}
world.afterEvents.itemUse.subscribe(e=>{if(e.itemStack.typeId==='minecraft:compass')system.run(()=>menu(e.source));});
system.afterEvents.scriptEventReceive.subscribe(e=>{if(e.sourceEntity?.typeId==='minecraft:player')system.run(()=>menu(e.sourceEntity));},{namespaces:['mapcraft']});
system.beforeEvents.startup.subscribe(e=>{
    try {
        e.customCommandRegistry.registerCommand({name:'mapcraft:menu',description:'現実の地図の設定',permissionLevel:CommandPermissionLevel.Any,cheatsRequired:false},origin=>{
            if(!origin.sourceEntity || origin.sourceEntity.typeId!=='minecraft:player')return {status:CustomCommandStatus.Failure};
            system.run(()=>menu(origin.sourceEntity));return {status:CustomCommandStatus.Success};
        });
    } catch (_) { /* compass and /scriptevent are available as alternatives */ }
});
