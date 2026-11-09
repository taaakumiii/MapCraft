"""Keep arrival geometry local to the generated region, not all of Japan."""


def regional_boundaries(towns,bounds,*,tolerance=.00001):
    """Clip OSM towns to generated geographic boxes; simplify about one metre.

    `bounds` is an iterable of (south, west, north, east), including islands.
    Keep IDs and holes so visits are stable and separate islands aren't filled.
    Stream source towns rather than building the nationwide geometry in memory.
    """
    from shapely.geometry import Polygon,box
    from shapely.ops import unary_union
    from shapely.prepared import prep
    coverage=unary_union([box(w,s,e,n) for s,w,n,e in bounds])
    if coverage.is_empty:return []
    selected=prep(coverage);result=[]
    def polygons(geometry):
        if geometry.geom_type=='Polygon':yield geometry
        elif hasattr(geometry,'geoms'):
            for part in geometry.geoms:yield from polygons(part)
    def ring(points):return [[round(x,7),round(y,7)] for x,y in points]
    for town in towns:
        if not selected.intersects(box(*town['bounds'])):continue
        outer=unary_union([Polygon(r).buffer(0) for r in town['outer']])
        if town.get('inner'):outer=outer.difference(unary_union([Polygon(r).buffer(0) for r in town['inner']]))
        if not selected.intersects(outer):continue
        area=outer.intersection(coverage).simplify(tolerance,preserve_topology=True)
        parts=[p for p in polygons(area) if p.area>0]
        if not parts:continue
        area=unary_union(parts)
        result.append({**town,'bounds':list(area.bounds),'outer':[ring(p.exterior.coords) for p in parts],
                       'inner':[ring(r.coords) for p in parts for r in p.interiors]})
    return result
