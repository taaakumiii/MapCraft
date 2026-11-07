import { world, system, BlockPermutation, CommandPermissionLevel, CustomCommandStatus } from '@minecraft/server';
import { bedPlans } from './bed-plans.js';
import { findIndoor, structural, support } from './treasure.js';

const prefix='mapcraft:beds_v1_',finished=new Set(),retries=new Map(),index=new Map();
const directions=[[0,1],[-1,0],[0,-1],[1,0]]; // Bedrock direction: south, west, north, east.
const CHUNK=64;
let job,scanAt=0,warned=false;
const registered=new Set();
export function registerBeds(plans,append=true){for(const plan of plans){
    if(registered.has(plan.id))continue;registered.add(plan.id);
    if(append)bedPlans.push(plan);
    for(const [x,z] of plan.points){
    const key=`${Math.floor(x/CHUNK)},${Math.floor(z/CHUNK)}`;
    if(!index.has(key))index.set(key,new Set());index.get(key).add(plan);
}}}
registerBeds(bedPlans,false);
function read(dimension,p){const block=dimension.getBlock(p);if(!block)throw Error('unloaded');return block;}
function save(plan,record){world.setDynamicProperty(prefix+plan.id,JSON.stringify(record));}
function saved(plan){const value=world.getDynamicProperty(prefix+plan.id);return typeof value==='string'?JSON.parse(value):undefined;}
function nearFurniture(block){return /(?:bed|chest|barrel|door)$/.test(block.typeId);}

function clearPair(dimension,pair){
    for(const p of [pair.foot,pair.head]){
        if(!read(dimension,p).isAir || !read(dimension,{...p,y:p.y+1}).isAir ||
           !support(read(dimension,{...p,y:p.y-1})))return false;
        // Leave doors and pre-existing furniture alone, including their access cells.
        for(const [dx,dz] of directions)if(nearFurniture(read(dimension,{x:p.x+dx,y:p.y,z:p.z+dz})))return false;
    }
    const [dx,dz]=directions[pair.direction],p=pair.foot;
    // A free side is needed so a player can approach the bed.
    return [-1,1].some(side=>[pair.foot,pair.head].every(q=>{
        const r={x:q.x+dz*side,y:q.y,z:q.z-dx*side};
        return read(dimension,r).isAir && read(dimension,{...r,y:r.y+1}).isAir && support(read(dimension,{...r,y:r.y-1}));
    }));
}

function* enclosedHead(dimension,plan,p){
    const [west,north,east,south]=plan.bounds;
    if(p.x<=west || p.x>=east || p.z<=north || p.z>=south)return false;
    let roof=false;
    for(let y=p.y+2;y<=Math.min(p.y+17,dimension.heightRange.max-1);y++){
        const block=read(dimension,{...p,y});yield;
        if(!block.isAir){roof=structural(block);break;}
    }
    if(!roof)return false;
    for(const [dx,dz] of directions){
        let wall=false;
        const limit=Math.min(64,dx>0?east-p.x+2:dx<0?p.x-west+2:dz>0?south-p.z+2:p.z-north+2);
        for(let step=1;step<=limit;step++){
            const block=read(dimension,{x:p.x+dx*step,y:p.y+1,z:p.z+dz*step});yield;
            if(structural(block) || /(?:door|glass_pane|iron_bars)$/.test(block.typeId)){wall=true;break;}
        }
        if(!wall)return false;
    }
    return true;
}

export function* findBedIndoor(dimension,plan){
    let incomplete=false;
    for(const point of plan.points){
        const result=yield* findIndoor(dimension,{...plan,points:[point]});
        if(result.retry){incomplete=true;continue;}
        if(!result.position)continue;
        for(let i=0;i<4;i++){
            const direction=((plan.seed>>>0)+i)%4,[dx,dz]=directions[direction],foot=result.position;
            const pair={foot,head:{x:foot.x+dx,y:foot.y,z:foot.z+dz},direction};
            try{
                if(clearPair(dimension,pair) && (yield* enclosedHead(dimension,plan,pair.head)))return {pair};
            }catch(_){incomplete=true;}
            yield;
        }
    }
    return incomplete?{retry:true}:{skip:true};
}

function matches(block,head,direction){
    return block.typeId==='minecraft:bed' && block.permutation.getState('head_piece_bit')===head && block.permutation.getState('direction')===direction;
}
export function placeBed(dimension,plan,pair){
    // Recheck immediately before mutation; a chest/player may have filled the
    // previously inspected space while this job yielded to the next tick.
    if(!clearPair(dimension,pair))return false;
    const foot=read(dimension,pair.foot),head=read(dimension,pair.head);
    const footState=BlockPermutation.resolve('minecraft:bed',{direction:pair.direction,head_piece_bit:false,occupied_bit:false});
    const headState=BlockPermutation.resolve('minecraft:bed',{direction:pair.direction,head_piece_bit:true,occupied_bit:false});
    save(plan,{status:'placing',pair});
    try{
        foot.setPermutation(footState);head.setPermutation(headState);
        if(!matches(foot,false,pair.direction) || !matches(head,true,pair.direction))throw Error('Bed pair incomplete');
        save(plan,{status:'placed',pair});finished.add(plan.id);return true;
    }catch(error){
        // Roll back only the exact halves created here, never a foreign block.
        for(const [block,isHead] of [[head,true],[foot,false]]){
            try{if(matches(block,isHead,pair.direction))block.setType('minecraft:air');}catch(_){}
        }
        save(plan,{status:'failed'});finished.add(plan.id);throw error;
    }
}

export function* processBed(dimension,plan){
    const record=saved(plan);
    if(record){
        if(record.status==='placing'){
            const foot=read(dimension,record.pair.foot),head=read(dimension,record.pair.head);yield;
            save(plan,{status:matches(foot,false,record.pair.direction)&&matches(head,true,record.pair.direction)?'placed':'interrupted',pair:record.pair});
        }
        // Taking/breaking a bed never creates a replacement, including after reload.
        finished.add(plan.id);return;
    }
    const result=yield* findBedIndoor(dimension,plan);
    if(result.pair){if(!placeBed(dimension,plan,result.pair))retries.set(plan.id,system.currentTick+200);}
    else if(result.retry)retries.set(plan.id,system.currentTick+200);
    else{save(plan,{status:'no_indoor_space'});finished.add(plan.id);}
}

system.runInterval(()=>{
    if(!job && system.currentTick>=scanAt){
        scanAt=system.currentTick+20;const nearby=new Set();
        for(const player of world.getAllPlayers()){
            if(player.dimension.id!=='minecraft:overworld')continue;
            const cx=Math.floor(player.location.x/CHUNK),cz=Math.floor(player.location.z/CHUNK);
            for(let dx=-1;dx<=1;dx++)for(let dz=-1;dz<=1;dz++)for(const plan of index.get(`${cx+dx},${cz+dz}`)||[])nearby.add(plan);
        }
        for(const plan of nearby){
            if(finished.has(plan.id)||(retries.get(plan.id)||0)>system.currentTick)continue;
            job={plan,iterator:processBed(world.getDimension('overworld'),plan)};break;
        }
    }
    if(job){try{for(let i=0;i<64;i++)if(job.iterator.next().done){job=undefined;break;}}
        catch(error){retries.set(job.plan.id,system.currentTick+200);job=undefined;if(!warned){console.warn('MapCraft beds: '+error);warned=true;}}}
},2);

system.beforeEvents.startup.subscribe(event=>{
    event.customCommandRegistry.registerCommand({name:'mapcraft:beds_status',description:'ベッドの生成状況（管理用）',permissionLevel:CommandPermissionLevel.Admin,cheatsRequired:true},origin=>{
        const player=origin.sourceEntity;if(player?.typeId!=='minecraft:player')return {status:CustomCommandStatus.Failure};
        system.run(()=>{
            const placed=[];for(const plan of bedPlans){const record=saved(plan);if(record?.status==='placed')placed.push(record.pair.foot);}
            placed.sort((a,b)=>Math.hypot(a.x-player.location.x,a.z-player.location.z)-Math.hypot(b.x-player.location.x,b.z-player.location.z));
            player.sendMessage(`ベッド：抽選対象 ${bedPlans.length}棟 / 配置済み ${placed.length}棟`);
            for(const p of placed.slice(0,3))player.sendMessage(`ベッド X${p.x} Y${p.y} Z${p.z}`);
        });return {status:CustomCommandStatus.Success};
    });
});
