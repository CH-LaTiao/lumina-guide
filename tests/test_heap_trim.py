import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from live_view import trim_heap


class HeapTrimTests(unittest.TestCase):
    def test_trim_heap_returns_bool_and_never_raises(self):
        self.assertIsInstance(trim_heap(),bool)


if __name__=='__main__':unittest.main()
