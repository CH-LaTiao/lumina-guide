import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import usb_reset


class UsbResetTests(unittest.TestCase):
    def test_find_camera_returns_node_or_none(self):
        node=usb_reset.find_camera()
        self.assertTrue(node is None or node.startswith('/dev/bus/usb/'))

    def test_reset_constant_matches_ioctl_encoding(self):
        self.assertEqual(usb_reset.USBDEVFS_RESET,(ord('U')<<8)|20)


if __name__=='__main__':unittest.main()
