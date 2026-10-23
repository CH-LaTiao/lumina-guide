"""RAM-only adjustable horizontal ground line bound to camera session."""
import math
import threading
import time

DEFAULT_RATIO=.55
MIN_RATIO=.20
MAX_RATIO=.95


class GroundLine:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.lock=threading.Lock();self.value=None

    def clear(self,info=None):
        with self.lock:self.value=None
        if info:
            geometry=info.get('geometry') or {};camera=geometry.get('camera') or {}
            session=info.get('session_id');height=camera.get('height')
            if session and camera.get('sha256') and type(height) is int and height>0:
                return self.read(session,camera['sha256'],height)
        return {'state':'DEFAULT','active':False,'line_y':None,'ratio':DEFAULT_RATIO,
            'min_ratio':MIN_RATIO,'max_ratio':MAX_RATIO}

    def set(self,payload,info):
        if payload.get('clear') is True:return self.clear(info)
        geometry=info.get('geometry') or {};camera=geometry.get('camera') or {}
        session=info.get('session_id')
        if not session or not camera.get('sha256'):
            raise ValueError('需要当前相机会话才能设置地面线')
        height=camera.get('height')
        if type(height) is not int or height<=0:raise ValueError('相机画幅无效')
        y=payload.get('y')
        if type(y) not in (int,float) or not math.isfinite(y):
            raise ValueError('地面线高度必须是有限数值')
        minimum,maximum=height*MIN_RATIO,height*MAX_RATIO
        if not minimum<=y<=maximum:
            raise ValueError(f'地面线必须在画面{int(MIN_RATIO*100)}%至{int(MAX_RATIO*100)}%之间')
        with self.lock:
            self.value={'session_id':session,'camera_sha256':camera['sha256'],
                'line_y':round(float(y),1),'created':self.clock()}
        return self.read(session,camera['sha256'],height)

    def read(self,session,camera_sha,height):
        default_y=round(height*DEFAULT_RATIO,1)
        with self.lock:
            value=self.value
            if value is None:
                return {'state':'DEFAULT','active':False,'line_y':default_y,
                    'ratio':DEFAULT_RATIO,'min_ratio':MIN_RATIO,'max_ratio':MAX_RATIO}
            if value['session_id']!=session or value['camera_sha256']!=camera_sha:
                self.value=None
                return {'state':'INVALIDATED','active':False,'line_y':default_y,
                    'ratio':DEFAULT_RATIO,'reason':'SESSION_OR_CAMERA_CHANGED',
                    'min_ratio':MIN_RATIO,'max_ratio':MAX_RATIO}
            line_y=value['line_y']
            return {'state':'SET','active':True,'line_y':line_y,
                'ratio':round(line_y/height,4),'min_ratio':MIN_RATIO,'max_ratio':MAX_RATIO,
                'session_id':session,'camera_sha256':camera_sha}
