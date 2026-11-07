import { world, system, ItemStack, CommandPermissionLevel, CustomCommandStatus } from '@minecraft/server';
import { treasurePlans } from './treasure-plans.js';

const prefix='mapcraft:treasure_v1_';
const finished=new Set(), retries=new Map();
const index=new Map();
let job, scanAt=0, warned=false;
const CHUNK=64;
const registered=new Set();

export function registerTreasure(plans,append=true){for(const plan of plans){
    if(registered.has(plan.id))continue;registered.add(plan.id);
    if(append)treasurePlans.push(plan);
    for(const [x,z] of plan.points){
    const key=`${Math.floor(x/CHUNK)},${Math.floor(z/CHUNK)}`;
    if(!index.has(key))index.set(key,new Set());index.get(key).add(plan);
}}}
registerTreasure(treasurePlans,false);
export { lootFor, random } from './loot.js';
import { lootFor, stackFor } from './loot.js';

function natural(block){return /(?:leaves|log|wood|stem|hyphae|grass|dirt|sand|gravel|water|lava|snow|ice|vine|flower|sapling|mushroom|bamboo|cactus|air)$/.test(block.typeId);}
export function structural(block){return block && !block.isAir && !block.isLiquid && !natural(block) &&
    /(?:planks|bricks?|brick_block|stonebrick|concrete|terracotta|wool|glass|quartz|sandstone|polished_|smooth_|purpur|prismarine|copper|tuff_brick|deepslate_tile|blackstone)/.test(block.typeId) &&
    !/(?:chest|barrel|torch|lantern|carpet|rail|ladder|fence|button|pressure_plate|trapdoor|door|pane|bars)$/.test(block.typeId);}
export function support(block){return structural(block) && !/(?:slab|stairs)$/.test(block.typeId);}
function read(dimension,x,y,z){const block=dimension.getBlock({x,y,z});if(!block)throw Error('unloaded');return block;}

export function* findIndoor(dimension,plan){
    let incomplete=false;
    for(const [x,z] of plan.points){
        try{
            const range=dimension.heightRange;
            let ceiling,solidRun=0;
            // Read an actual ceiling, a two-block air space and a solid floor.
            // Searching downward naturally avoids roof surfaces and upper leaves.
            for(let y=range.max-2;y>range.min+1;y--){
                const block=read(dimension,x,y,z);yield;
                if(block.isAir){solidRun=0;continue;}
                if(++solidRun>=8)break;
                if(support(block) && ceiling!==undefined && ceiling-y>=3 && ceiling-y<=18){
                    const a=read(dimension,x,y+1,z),b=read(dimension,x,y+2,z);yield;
                    if(a.isAir && b.isAir){
                        let enclosed=0;
                        const [west,north,east,south]=plan.bounds;
                        for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1]]){
                            const limit=Math.min(64,dx>0?east-x+2:dx<0?x-west+2:dz>0?south-z+2:z-north+2);
                            for(let step=1;step<=limit;step++){
                                const wall=read(dimension,x+dx*step,y+2,z+dz*step);yield;
                                if(structural(wall) || /(?:door|glass_pane|iron_bars)$/.test(wall.typeId)){enclosed++;break;}
                            }
                        }
                        // Four walls keep open porches/courtyards out. Skip a
                        // doorway column rather than modifying it to fit a chest.
                        if(enclosed===4){
                            let clear=true;
                            for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1]]){
                                const side=read(dimension,x+dx,y+1,z+dz);yield;
                                if(/(?:chest|barrel|door)$/.test(side.typeId))clear=false;
                            }
                            if(clear)return {position:{x,y:y+1,z}};
                        }
                    }
                }
                ceiling=structural(block)?y:undefined;
            }
        }catch(error){incomplete=true;}
    }
    return incomplete?{retry:true}:{skip:true};
}

function saved(plan){const value=world.getDynamicProperty(prefix+plan.id);return typeof value==='string'?JSON.parse(value):undefined;}
function persist(plan,value){world.setDynamicProperty(prefix+plan.id,JSON.stringify(value));}
function place(dimension,plan,position){
    const target=read(dimension,position.x,position.y,position.z);
    const above=read(dimension,position.x,position.y+1,position.z);
    if(!target.isAir || !above.isAir)return false;
    if(!support(read(dimension,position.x,position.y-1,position.z)))return false;
    for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1]]){
        if(/(?:chest|barrel|door)$/.test(read(dimension,position.x+dx,position.y,position.z+dz).typeId))return false;
    }
    // Store the location before mutation so an interruption never creates a
    // second chest. A saved existing chest is never refilled on recovery.
    const items=lootFor(plan).map(item=>({slot:item.slot,stack:stackFor(item)}));
    persist(plan,{status:'placing',position});
    target.setType('minecraft:chest');
    const container=target.getComponent('minecraft:inventory')?.container;
    if(!container)throw Error('Chest inventory unavailable');
    for(const item of items)container.setItem(item.slot,item.stack);
    persist(plan,{status:'placed',position});finished.add(plan.id);return true;
}

export function* processBuilding(dimension,plan){
    const record=saved(plan);
    if(record){
        if(record.status!=='placing'){finished.add(plan.id);return;}
        const block=read(dimension,record.position.x,record.position.y,record.position.z);yield;
        if(block.typeId==='minecraft:chest'){persist(plan,{status:'placed',position:record.position});finished.add(plan.id);return;}
        if(place(dimension,plan,record.position))return;
        persist(plan,{status:'blocked'});finished.add(plan.id);return;
    }
    const result=yield* findIndoor(dimension,plan);
    if(result.position){if(!place(dimension,plan,result.position))retries.set(plan.id,system.currentTick+200);}
    else if(result.retry)retries.set(plan.id,system.currentTick+200);
    else{persist(plan,{status:'no_indoor_space'});finished.add(plan.id);}
}

system.runInterval(()=>{
    if(!job && system.currentTick>=scanAt){
        scanAt=system.currentTick+20;
        const nearby=new Set();
        for(const player of world.getAllPlayers()){
            if(player.dimension.id!=='minecraft:overworld')continue;
            const cx=Math.floor(player.location.x/CHUNK),cz=Math.floor(player.location.z/CHUNK);
            for(let dx=-1;dx<=1;dx++)for(let dz=-1;dz<=1;dz++)for(const plan of index.get(`${cx+dx},${cz+dz}`)||[])nearby.add(plan);
        }
        for(const plan of nearby){
            if(finished.has(plan.id) || (retries.get(plan.id)||0)>system.currentTick)continue;
            job={plan,iterator:processBuilding(world.getDimension('overworld'),plan)};break;
        }
    }
    if(job){
        try{for(let i=0;i<64;i++){if(job.iterator.next().done){job=undefined;break;}}}
        catch(error){retries.set(job.plan.id,system.currentTick+200);job=undefined;if(!warned){console.warn('MapCraft treasure: '+error);warned=true;}}
    }
},2);

// Operator-only inspection: ordinary players discover the chests by exploring.
system.beforeEvents.startup.subscribe(event=>{
    event.customCommandRegistry.registerCommand({name:'mapcraft:treasure_status',description:'宝箱の生成状況（管理用）',
        permissionLevel:CommandPermissionLevel.Admin,cheatsRequired:true},origin=>{
        const player=origin.sourceEntity;
        if(!player || player.typeId!=='minecraft:player')return {status:CustomCommandStatus.Failure};
        system.run(()=>{
            const placed=[];let skipped=0;
            for(const plan of treasurePlans){const record=saved(plan);
                if(record?.status==='placed')placed.push({...record,theme:plan.theme});
                else if(record?.status==='no_indoor_space')skipped++;
            }
            placed.sort((a,b)=>Math.hypot(a.position.x-player.location.x,a.position.z-player.location.z)-Math.hypot(b.position.x-player.location.x,b.position.z-player.location.z));
            player.sendMessage(`宝箱：抽選対象 ${treasurePlans.length}棟 / 配置済み ${placed.length}棟 / 屋内なし ${skipped}棟`);
            for(const record of placed.slice(0,3)){const p=record.position;player.sendMessage(`宝箱 X${p.x} Y${p.y} Z${p.z}（${record.theme}）`);}
        });
        return {status:CustomCommandStatus.Success};
    });
});
