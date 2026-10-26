import collections
import re
import unittest
from pathlib import Path


class LiveViewJavaScriptTests(unittest.TestCase):
    def test_region_table_uses_a_defined_constant(self):
        root=Path(__file__).resolve().parents[1]
        script=(root/'tools/live_view.js').read_text(encoding='utf-8')
        self.assertIn('cameraRegionNames[i]',script)
        self.assertNotIn('[regionNames[i]',script)

    def test_page_cache_busts_the_fixed_script(self):
        root=Path(__file__).resolve().parents[1]
        page=(root/'tools/live_view.html').read_text(encoding='utf-8')
        import sys
        sys.path.insert(0,str(root/'tools'))
        from software_version import VERSION
        self.assertIn('/live_view.js?v='+VERSION,page)

    def test_perception_board_and_ground_line_controls_exist(self):
        root=Path(__file__).resolve().parents[1]
        page=(root/'tools/live_view.html').read_text(encoding='utf-8')
        for value in ('class="perception-board"','id="ground-line-canvas"','id="ground-line-edit"',
                'id="ground-line-reset"','id="speech-text"','id="detview"','id="geometry-objects"'):
            self.assertIn(value,page)

    def test_ids_are_unique_and_all_script_references_exist(self):
        root=Path(__file__).resolve().parents[1]
        page=(root/'tools/live_view.html').read_text(encoding='utf-8')
        script=(root/'tools/live_view.js').read_text(encoding='utf-8')
        ids=re.findall(r'id="([^"]+)"',page)
        duplicates={key:count for key,count in collections.Counter(ids).items() if count>1}
        self.assertEqual(duplicates,{})
        references=set(re.findall(r"\$\('([^']+)'\)",script))
        missing=sorted(value for value in references
            if value not in ids and not value.startswith('motor-') and value!='detobjects')
        self.assertEqual(missing,[])


if __name__=='__main__':unittest.main()
