"""Geography and Arnis 3.4 command construction; no UI dependencies."""
import math
from pathlib import Path

ENGINE_SHA256 = '201f67720148e6262e8adbbf51a9bb29fc31364439f740447cf67b42a1d9b0f9'
SCALES = {'2m = 1ブロック（広範囲向け）': .5, '1m = 1ブロック（等倍）': 1.,
          '約3m = 1ブロック（軽量）': .34}
MODES = {'道路・建物 ＋ 実際の地形': 'geo-terrain', '道路・建物 ＋ 平地': 'geo-only',
         '地形のみ': 'terrain-only', '道路 ＋ 建物の輪郭': 'blueprint'}

def pixel(lat, lon, zoom):
    lat = max(-85.0511, min(85.0511, lat))
    size = 256 * 2 ** zoom
    return (lon + 180) / 360 * size, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size

def latlon(x, y, zoom):
    size = 256 * 2 ** zoom
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2*y/size)))), x / size * 360 - 180

def validate_bbox(bbox):
    if len(bbox) != 4 or not all(math.isfinite(x) for x in bbox):
        raise ValueError('座標は南緯度・西経度・北緯度・東経度の4つの数値で入力してください。')
    s, w, n, e = bbox
    if not (-85 < s < n < 85 and -180 <= w < e <= 180):
        raise ValueError('南 < 北、西 < 東になる有効な座標を指定してください。')
    return tuple(bbox)

def dimensions(bbox, scale=1.):
    s, w, n, e = validate_bbox(bbox)
    width = math.radians(e-w) * 6371008.8 * math.cos(math.radians((n+s)/2))
    height = math.radians(n-s) * 6371008.8
    return width, height, max(1, round(width*scale)), max(1, round(height*scale))

def square(lat, lon, metres):
    dy = math.degrees(metres/2/6371008.8)
    dx = dy/math.cos(math.radians(lat))
    return validate_bbox((lat-dy, lon-dx, lat+dy, lon+dx))

def command(engine, bbox, output, *, edition='java', scale=.5, mode='geo-terrain',
            height=1., vanilla=True, overture=True, trees=True, local_file=None, spawn=None,fillground=True):
    bbox = validate_bbox(bbox)
    if not .3 <= scale <= 4:
        raise ValueError('道路・建物を維持するため縮尺は0.3〜4ブロック/mにしてください。')
    if edition not in ('java', 'bedrock') or mode not in MODES.values():
        raise ValueError('出力形式または生成モードが不正です。')
    if not .1 <= height <= 10:
        raise ValueError('地形の高さ倍率は0.1〜10です。')
    s,w,n,e = bbox
    if spawn is None or not (s <= spawn[0] <= n and w <= spawn[1] <= e):
        spawn = ((s+n)/2, (w+e)/2)
    args = [str(Path(engine).resolve()), '--bbox='+','.join(f'{v:.7f}' for v in bbox),
            '--output-dir', str(Path(output).resolve()), '--scale', str(scale), '--mode', mode,
            '--height-multiplier', str(height), '--overture', str(overture).lower(),
            '--canopy-height', str(trees).lower(), '--spawn-lat', str(spawn[0]), '--spawn-lng', str(spawn[1]),
            '--gamemode', 'creative', '--map-item', 'false', '--no-3d']
    if vanilla:
        args.append('--vanilla-height')
    if fillground:args.extend(['--fillground','--ground-level','64'])
    if edition == 'bedrock':
        args.append('--bedrock')
        if not local_file:args += ['--save-json-file',str(Path(output).resolve()/'osm.json')]
    if local_file:
        args += ['--file', str(Path(local_file).resolve())]
    return args

def find_worlds(folder, edition):
    folder = Path(folder)
    if edition == 'bedrock':
        return sorted(folder.glob('*.mcworld'))
    return sorted(p.parent for p in folder.glob('*/level.dat') if (p.parent/'region').exists())
