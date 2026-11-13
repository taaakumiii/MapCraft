from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageColor
from map_water import LAND_COLOUR,WATER_COLOUR,ocean_area,water_areas,paint_water
from realmap import prepare_map
from tracking import MapReference


class WaterCartography(unittest.TestCase):
    def render(self,coasts=(),areas=()):
        image=Image.new('RGB',(100,100),LAND_COLOUR)
        paint_water(image,[ocean_area(coasts,(0,0,99,99)),*areas])
        return image

    def assert_colour(self,image,point,colour):
        self.assertEqual(image.getpixel(point),ImageColor.getrgb(colour))

    def test_open_coastline_sea_side_and_direction(self):
        coast=[(50,-10),(50,110)]
        image=self.render([coast])
        self.assert_colour(image,(25,50),WATER_COLOUR)
        self.assert_colour(image,(75,50),LAND_COLOUR)
        reverse=self.render([list(reversed(coast))])
        self.assert_colour(reverse,(25,50),LAND_COLOUR)
        self.assert_colour(reverse,(75,50),WATER_COLOUR)

    def test_island_and_open_ocean_beyond_view(self):
        # CCW in geographic coordinates; north/south is reversed on screen.
        coast=[(25,25),(25,75),(75,75),(75,25),(25,25)]
        image=self.render([coast])
        self.assert_colour(image,(50,50),LAND_COLOUR)
        self.assert_colour(image,(10,50),WATER_COLOUR)
        offshore=self.render([[(120,-10),(120,110)]])
        self.assert_colour(offshore,(50,50),WATER_COLOUR)

    def test_bay_assembled_from_ways_and_dangling_data_is_safe(self):
        coasts=[[(50,-10),(50,25),(80,25)],[(80,25),(80,75),(50,75),(50,110)]]
        image=self.render(coasts)
        self.assert_colour(image,(60,50),WATER_COLOUR)
        self.assert_colour(image,(90,50),LAND_COLOUR)
        broken=self.render([[(50,-10),(50,50)]])
        self.assert_colour(broken,(25,50),LAND_COLOUR)
        self.assert_colour(broken,(75,50),LAND_COLOUR)

    def test_multipolygon_lake_preserves_island_and_split_rings(self):
        def geometry(points):return [{'lat':y,'lon':x} for x,y in points]
        outer=[(10,10),(90,10),(90,90),(10,90),(10,10)]
        hole=[(40,40),(60,40),(60,60),(40,60),(40,40)]
        members=[{'type':'way','ref':1,'role':'outer','geometry':geometry(outer[:3])},
                 {'type':'way','ref':2,'role':'outer','geometry':geometry(outer[2:])},
                 {'type':'way','ref':3,'role':'inner','geometry':geometry(hole)}]
        elements=[{'type':'relation','id':4,'tags':{'natural':'water','type':'multipolygon'},'members':members},
                  {'type':'way','id':3,'tags':{'natural':'water'},'geometry':geometry(hole)}]
        areas=water_areas(elements,{},lambda lat,lon:(lon,lat))
        image=self.render(areas=areas)
        self.assert_colour(image,(20,50),WATER_COLOUR)
        self.assert_colour(image,(50,50),LAND_COLOUR)
        self.assert_colour(image,(5,50),LAND_COLOUR)

    def test_renderer_replaces_old_cache_and_draws_river_blue(self):
        ref=MapReference('d114e8c5-08d9-4fbc-a756-e20c523c155e',(35,139,35.01,139.01),1,1000,1000)
        payload={'elements':[{'type':'way','id':1,'tags':{'waterway':'river'},
                             'geometry':[{'lat':35.005,'lon':138.99},{'lat':35.005,'lon':139.02}]}]}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'osm.json').write_text(json.dumps(payload),encoding='utf-8')
            _,atlas=prepare_map(ref,root=root,zooms=[14])
            (root/'map_z14.png').write_bytes(b'old cached image')
            atlas.pop('render_key');(root/'atlas.json').write_text(json.dumps(atlas),encoding='utf-8')
            _,fresh=prepare_map(ref,root=root,zooms=[14])
            self.assertIn('render_key',fresh)
            with Image.open(root/'map_z14.png') as image:
                self.assertIn(ImageColor.getrgb(WATER_COLOUR),image.getdata())


if __name__=='__main__':unittest.main()
