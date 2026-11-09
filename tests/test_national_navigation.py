from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from national_navigation import regional_boundaries
try:
    from shapely.geometry import Polygon,Point
    HAS_GEOMETRY=True
except ImportError:HAS_GEOMETRY=False


@unittest.skipUnless(HAS_GEOMETRY,'Bundled geometry runtime required')
class RegionalNavigation(unittest.TestCase):
    def town(self,identity,bounds,holes=None):
        w,s,e,n=bounds
        return {'id':identity,'name':identity,'bounds':bounds,'outer':[[[w,s],[e,s],[e,n],[w,n],[w,s]]],'inner':holes or []}
    def test_excludes_distant_towns_and_preserves_town_ids_and_holes(self):
        hole=[[3,3],[4,3],[4,4],[3,4],[3,3]]
        towns=[self.town('local',[0,0,10,10],[hole]),self.town('distant',[100,100,110,110])]
        result=regional_boundaries(iter(towns),[(2,2,8,8)])
        self.assertEqual(len(result),1);self.assertEqual(result[0]['id'],'local')
        self.assertEqual(result[0]['bounds'],[2,2,8,8]);self.assertEqual(len(result[0]['inner']),1)
        area=Polygon(result[0]['outer'][0],result[0]['inner'])
        self.assertFalse(area.covers(Point(3.5,3.5)));self.assertTrue(area.covers(Point(5,5)))
    def test_separate_islands_do_not_include_the_gap(self):
        town=self.town('islands',[0,0,10,10]);result=regional_boundaries([town],[(0,0,2,2),(8,8,10,10)])
        self.assertEqual(len(result[0]['outer']),2)
        self.assertFalse(any(Polygon(r).covers(Point(5,5)) for r in result[0]['outer']))
        self.assertEqual(regional_boundaries([town],[]),[])


if __name__=='__main__':unittest.main()
