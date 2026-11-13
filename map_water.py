"""Offline OSM water cartography, including directed coastlines and lake islands."""
from PIL import Image, ImageDraw

WATER_COLOUR = '#69b9e6'
LAND_COLOUR = '#f2f1eb'
RENDER_STYLE = 2


def is_water(tags):
    return (tags.get('natural') == 'water' or
            tags.get('waterway') == 'riverbank' or
            tags.get('landuse') in ('reservoir', 'basin') or
            tags.get('water') in ('lake', 'pond', 'reservoir', 'river', 'canal',
                                  'basin', 'lagoon', 'sea', 'ocean'))


def polygons(geometry):
    if geometry.geom_type == 'Polygon':
        yield geometry
    elif hasattr(geometry, 'geoms'):
        for part in geometry.geoms:
            yield from polygons(part)


def water_areas(elements, nodes, project):
    """Keep relation inner rings dry; do not paint member ways a second time."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from municipalities import join_rings
    shapes = []
    members = set()
    for item in elements:
        if item.get('type') != 'relation' or not is_water(item.get('tags', {})):
            continue
        outer, inner = [], []
        for member in item.get('members', []):
            if member.get('type') != 'way':
                continue
            points = [project(p['lat'], p['lon']) for p in member.get('geometry', []) if p and 'lat' in p]
            if points:
                (inner if member.get('role') == 'inner' else outer).append(points)
        try:
            area = unary_union([Polygon(r).buffer(0) for r in join_rings(outer)])
            holes = unary_union([Polygon(r).buffer(0) for r in join_rings(inner)])
        except ValueError:
            # An incomplete relation must not flood an island or a whole view.
            continue
        shapes.append(area.difference(holes))
        members.update(m['ref'] for m in item.get('members', []) if m.get('type') == 'way')
    for item in elements:
        if item.get('type') != 'way' or item.get('id') in members or not is_water(item.get('tags', {})):
            continue
        geometry = item.get('geometry') or [{'lat': nodes[i][0], 'lon': nodes[i][1]}
                                            for i in item.get('nodes', []) if i in nodes]
        points = [project(p['lat'], p['lon']) for p in geometry if p and 'lat' in p]
        if len(points) >= 4 and points[0] == points[-1]:
            shapes.append(Polygon(points).buffer(0))
    return shapes


def ocean_area(coasts, bounds):
    """Classify the sea side of an OSM directed coastline in pixel coordinates.

    Node the coastline against the view boundary, then classify each resulting
    face using its nearest original directed segment. Never close an open way
    with an arbitrary straight line across a bay.
    https://wiki.openstreetmap.org/wiki/Tag:natural=coastline
    """
    from shapely.geometry import LineString, box
    from shapely.ops import polygonize, unary_union
    from shapely.strtree import STRtree
    lines = [LineString(p) for p in coasts if len(p) > 1]
    if not lines:
        return None
    view = box(*bounds)
    # A local extract may include a relation's coastline ways from all Japan.
    # Index whole ways first; only split/union lines near the image viewport.
    line_tree = STRtree(lines)
    margin = max(bounds[2]-bounds[0], bounds[3]-bounds[1])
    nearby = line_tree.query(view.buffer(margin), predicate='intersects')
    selected = [lines[int(i)] for i in nearby] if len(nearby) else [lines[int(line_tree.nearest(view))]]
    segments = [LineString([a, b]) for line in selected for a, b in zip(line.coords, list(line.coords)[1:]) if a != b]
    if not segments:
        return None
    shore = unary_union(selected).intersection(view)
    faces = polygonize(unary_union([view.boundary, shore]))
    tree = STRtree(segments)
    water = []
    for face in faces:
        if not view.covers(face.representative_point()):
            continue
        # A dangling, incomplete coastline cannot safely divide this face.
        if shore.intersection(face).difference(face.boundary.buffer(1e-6)).length > 1e-5:
            continue
        point = face.representative_point()
        segment = segments[int(tree.nearest(point))]
        (ax, ay), (bx, by) = segment.coords
        cross = (bx-ax)*(point.y-ay) - (by-ay)*(point.x-ax)
        if cross > 0:  # Pixel y increases southward, reversing geographic handedness.
            water.append(face)
    return unary_union(water)


def paint_water(image, areas):
    """Union before masking so one island cannot erase overlapping water."""
    from shapely.ops import unary_union
    mask = Image.new('L', image.size)
    draw = ImageDraw.Draw(mask)
    for area in polygons(unary_union([a for a in areas if a is not None])):
        draw.polygon(list(area.exterior.coords), fill=255)
        for hole in area.interiors:
            draw.polygon(list(hole.coords), fill=0)
    image.paste(WATER_COLOUR, (0, 0), mask)
