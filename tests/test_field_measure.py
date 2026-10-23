import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from field_measure import summarise, take_row


class FieldMeasureTests(unittest.TestCase):
    def test_error_and_summary(self):
        status={'range_center_m':1.2,'valid_depth_ratio':0.8,'frame_age_ms':90,'frame_id':'s:1'}
        row=take_row(status,'glass',1.0)
        self.assertAlmostEqual(row['abs_error_m'],0.2)
        self.assertEqual(row['tag'],'glass')
        report=summarise([row])
        self.assertEqual(report['valid'],1)
        self.assertAlmostEqual(report['mae_m'],0.2)

    def test_missing_center_range_counts_as_missing(self):
        row=take_row({'range_center_m':None,'valid_depth_ratio':0.1},'dark',2.0)
        report=summarise([row])
        self.assertEqual(report['valid'],0)
        self.assertEqual(report['missing'],1)
        self.assertIsNone(report['mae_m'])

    def test_p95_uses_nearest_rank(self):
        rows=[{'abs_error_m':value} for value in (0.01,0.02,0.03,0.04,0.5)]
        self.assertEqual(summarise(rows)['p95_m'],0.5)


if __name__=='__main__':unittest.main()
