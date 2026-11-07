// MapCraft addition. Replaced with the chosen world's reference while packaging.
export const config = /*MAPCRAFT_CONFIG*/null;

export function geographic(x,z) {
    if(config.projection==='national_mercator') {
        const [a,b]=config.origin,k=config.scale*Math.cos(a*Math.PI/180),r=6371000;
        const y=r*Math.log(Math.tan(Math.PI/4+a*Math.PI/360))-z/k;
        return [(2*Math.atan(Math.exp(y/r))-Math.PI/2)*180/Math.PI,b+x/(r*k)*180/Math.PI];
    }
    const [s,w,n,e]=config.bbox;
    return [n-z/config.span_z*(n-s),w+x/config.span_x*(e-w)];
}

export function contains(x,z) {
    const [lat,lon]=geographic(x,z),[s,w,n,e]=config.bbox;
    return lat>=s && lat<=n && lon>=w && lon<=e;
}

export function geographicLine(player) {
    if (!config || player.dimension.id !== 'minecraft:overworld') return null;
    const { x, z } = player.location;
    if (!contains(x,z)) return 'Outside generated area';
    const [lat,lon]=geographic(x,z);
    return `N${lat.toFixed(5)} E${lon.toFixed(5)}`;
}
