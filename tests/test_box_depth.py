import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from box_depth import frame_index, iou, link


class BoxDepthTests(unittest.TestCase):
    def test_iou_identical_and_disjoint(self):
        self.assertAlmostEqual(iou([0,0,10,10],[0,0,10,10]),1.0)
        self.assertEqual(iou([0,0,10,10],[20,20,30,30]),0.0)

    def test_overlapping_cluster_supplies_distance(self):
        objects=[{'label':'chair','bbox':[10,10,110,110],'depth_patch_m':2.5}]
        clusters=[{'cluster_id':3,'bbox':[12,12,108,108],'depth_z_p20_m':1.9}]
        result=link(objects,clusters,"s:1","s:1")[0]
        self.assertEqual(result['depth_source'],'cluster')
        self.assertEqual(result['cluster_distance_m'],1.9)
        self.assertGreater(result['cluster_iou'],0.9)

    def test_non_overlapping_cluster_keeps_patch(self):
        objects=[{'label':'chair','bbox':[0,0,20,20],'depth_patch_m':2.5}]
        clusters=[{'cluster_id':1,'bbox':[100,100,200,200],'depth_z_p20_m':1.2}]
        self.assertEqual(link(objects,clusters)[0]['depth_source'],'patch')

    def test_stale_geometry_frame_is_ignored(self):
        objects=[{'bbox':[0,0,10,10]}]
        clusters=[{'cluster_id':1,'bbox':[0,0,10,10],'depth_z_p20_m':1.0}]
        result=link(objects,clusters,'s:100','s:90')[0]
        self.assertEqual(result['depth_source'],'patch')

    def test_frame_index_parsing(self):
        self.assertEqual(frame_index('abc:42'),42)
        self.assertIsNone(frame_index('bad'))
