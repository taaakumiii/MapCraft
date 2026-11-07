import { world, system } from '@minecraft/server';

const config = /*MAPCRAFT_CONFIG*/;
let seq = 0;
const directions = ['南', '南西', '西', '北西', '北', '北東', '東', '南東'];

system.runInterval(() => {
    seq++;
    for (const player of world.getAllPlayers()) {
        try {
            const location = player.location;
            const yaw = player.getRotation().y;
            if (player.dimension.id !== 'minecraft:overworld') {
                player.onScreenDisplay.setActionBar('MapCraft GPS：このディメンションには実地図がありません');
                continue;
            }
            const [south, west, north, east] = config.bbox;
            const lat = north - location.z / config.span_z * (north - south);
            const lon = west + location.x / config.span_x * (east - west);
            const inside = location.x >= 0 && location.x <= config.span_x && location.z >= 0 && location.z <= config.span_z;
            const direction = directions[((Math.round(yaw / 45) % 8) + 8) % 8];
            player.onScreenDisplay.setActionBar(
                `§bMapCraft GPS§r  北緯 ${lat.toFixed(5)}  東経 ${lon.toFixed(5)}\n` +
                `${direction}向き  X ${Math.floor(location.x)} / Z ${Math.floor(location.z)}` +
                (inside ? '' : '  §e生成範囲外§r')
            );
            // Only this prefixed record is consumed by the local companion app.
            console.warn('MAPCRAFT_GPS ' + JSON.stringify({
                map: config.map_id, pid: player.id, player: player.name,
                x: location.x, y: location.y, z: location.z, yaw,
                dimension: player.dimension.id, t: Date.now(), seq
            }));
        } catch (_) {
            // Players may leave between enumeration and reading their location.
        }
    }
}, 10);
