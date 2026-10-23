"""Reset the Orbbec USB device after a hard kill left the UVC stream wedged.

Requires root (write access to /dev/bus/usb/BBB/DDD):
    sudo python3 tools/usb_reset.py
    python3 tools/usb_reset.py --print   # show the node without resetting
"""
import ctypes
import glob
import os
import sys

USBDEVFS_RESET=0x5514


def find_camera():
    for path in glob.glob('/sys/bus/usb/devices/*'):
        try:
            with open(f'{path}/idVendor',encoding='ascii') as handle:
                if handle.read().strip()!='2bc5':continue
            with open(f'{path}/busnum',encoding='ascii') as handle:bus=int(handle.read().strip())
            with open(f'{path}/devnum',encoding='ascii') as handle:dev=int(handle.read().strip())
            return f'/dev/bus/usb/{bus:03d}/{dev:03d}'
        except (OSError,ValueError):
            continue
    return None


def main():
    node=find_camera()
    if not node:
        print('Orbbec camera not found on USB')
        return 1
    if '--print' in sys.argv:
        print(node)
        return 0
    if os.geteuid()!=0:
        print(f'root required to reset {node}; run: sudo python3 tools/usb_reset.py')
        return 2
    fd=os.open(node,os.O_WRONLY)
    try:result=ctypes.CDLL('libc.so.6').ioctl(fd,USBDEVFS_RESET,0)
    finally:os.close(fd)
    print(f'reset {node} result={result}')
    return 0 if result==0 else 1


if __name__=='__main__':raise SystemExit(main())
