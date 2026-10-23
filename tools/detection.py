"""Bounded ONNX object detection for diagnostic snapshots, not movement control.

Prefers the CUDA provider and falls back to CPU so a missing GPU runtime degrades
instead of stopping the preview; the active provider is reported to the UI.
"""
import ast
import hashlib
import threading
import time
from pathlib import Path
import cv2
import numpy as np
from detection_frames import DetectionFrames

def prepare(image):
    h,w=image.shape[:2];scale=min(640/w,640/h)
    nw,nh=round(w*scale),round(h*scale);left=(640-nw)//2;top=(640-nh)//2
    canvas=np.full((640,640,3),114,np.uint8)
    canvas[top:top+nh,left:left+nw]=cv2.resize(image,(nw,nh))
    tensor=np.ascontiguousarray(canvas[:,:,::-1].transpose(2,0,1)[None],dtype=np.float32)/255
    return tensor,(scale,left,top,w,h)

def decode(output,transform,names,threshold=.4):
    if output.shape!=(1,84,8400):raise ValueError('Unexpected YOLO11 COCO output contract')
    scale,left,top,w,h=transform;rows=output[0].T
    if not np.isfinite(rows).all():raise ValueError('Non-finite detector output')
    classes=rows[:,4:].argmax(1);scores=rows[np.arange(len(rows)),classes+4]
    keep=scores>=threshold;rows=rows[keep];classes=classes[keep];scores=scores[keep]
    candidates=[]
    for label in np.unique(classes):
        idx=np.flatnonzero(classes==label);r=rows[idx]
        boxes=np.column_stack((r[:,0]-r[:,2]/2,r[:,1]-r[:,3]/2,r[:,2],r[:,3])).tolist()
        chosen=cv2.dnn.NMSBoxes(boxes,scores[idx].tolist(),threshold,.45)
        for k in np.asarray(chosen).flatten():
            j=idx[k];cx,cy,bw,bh=rows[j,:4]
            x1=max(0,min(w-1,float((cx-bw/2-left)/scale)));y1=max(0,min(h-1,float((cy-bh/2-top)/scale)))
            x2=max(0,min(w,float((cx+bw/2-left)/scale)));y2=max(0,min(h,float((cy+bh/2-top)/scale)))
            if x2<=x1 or y2<=y1:continue
            candidates.append({'class_id':int(label),'label':names[int(label)],'score':float(scores[j]),'bbox':[x1,y1,x2,y2]})
    return sorted(candidates,key=lambda r:-r['score'])[:30]

def patch_depth(depth,box,scale_mm):
    x1,y1,x2,y2=box;cx=(x1+x2)/2;cy=(y1+y2)/2
    rx=max(2,(x2-x1)*.15);ry=max(2,(y2-y1)*.15)
    h,w=depth.shape
    roi=depth[max(0,int(cy-ry)):min(h,int(cy+ry)+1),max(0,int(cx-rx)):min(w,int(cx+rx)+1)]
    values=roi[roi>0].astype(np.float32)*scale_mm/1000
    ratio=float(len(values)/roi.size) if roi.size else 0
    if ratio<.6 or len(values)<9:return {'depth_patch_m':None,'depth_valid_ratio':ratio,'depth_reason':'insufficient_depth'}
    return {'depth_patch_m':float(np.median(values)),'depth_valid_ratio':ratio,'depth_reason':'aligned_center_patch_not_object_surface_guarantee'}

class Detector:
    def __init__(self,model):
        self.model=Path(model);self.lock=threading.Lock();self.pending=None;self.result=None;self.stop=threading.Event();self.generation=0
        self.frames=DetectionFrames()
        self.status='LOADING';self.provider='LOADING';self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def submit(self,rgb,depth,scale,frame_id,captured):
        with self.lock:self.pending=(rgb.copy(),depth.copy(),scale,frame_id,captured,self.generation)
    def invalidate(self):
        with self.lock:self.generation+=1;self.pending=None;self.result=None;self.frames.clear()
    def read_frame(self,frame_id):
        with self.lock:return self.frames.get(frame_id)
    def read(self):
        with self.lock:
            result=self.result
            if result and time.monotonic()-result['captured']>2:return {'status':'STALE','objects':[]},None
            if result:return {k:v for k,v in result.items() if k not in ('jpeg','captured')}|{
                'age_ms':round((time.monotonic()-result['captured'])*1000,1)},result['jpeg']
            return {'status':'WAITING' if self.status=='READY' else self.status,'objects':[]},None
    def run(self):
        try:
            import onnxruntime as ort
            opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
            available=ort.get_available_providers()
            order=[p for p in ('CUDAExecutionProvider',) if p in available]+['CPUExecutionProvider']
            session=ort.InferenceSession(str(self.model),sess_options=opts,providers=order)
            active=session.get_providers()
            self.provider='CUDA' if active and 'CUDA' in active[0] else 'CPU'
            names=ast.literal_eval(session.get_modelmeta().custom_metadata_map['names'])
            model_sha=hashlib.sha256(self.model.read_bytes()).hexdigest();self.status='READY'
            while not self.stop.is_set():
                with self.lock:item=self.pending;self.pending=None
                if item is None:self.stop.wait(.03);continue
                rgb,depth,scale,frame_id,captured,generation=item
                if time.monotonic()-captured>.5:continue
                started=time.monotonic();tensor,transform=prepare(rgb)
                result=session.run(None,{'images':tensor})[0];objects=decode(result,transform,names)
                for obj in objects:
                    obj.update(patch_depth(depth,obj['bbox'],scale))
                    x1,y1,x2,y2=map(int,obj['bbox']);cv2.rectangle(rgb,(x1,y1),(x2,y2),(70,230,110),2)
                    distance='depth ?' if obj['depth_patch_m'] is None else f"patch {obj['depth_patch_m']:.2f}m"
                    text=f"{obj['label']} {obj['score']:.2f} / {distance}"
                    tw=cv2.getTextSize(text,cv2.FONT_HERSHEY_SIMPLEX,.45,1)[0][0]
                    origin=(max(0,min(x1,rgb.shape[1]-tw-3)),max(18,y1-5))
                    cv2.putText(rgb,text,origin,cv2.FONT_HERSHEY_SIMPLEX,.45,(0,0,0),3,cv2.LINE_AA)
                    cv2.putText(rgb,text,origin,cv2.FONT_HERSHEY_SIMPLEX,.45,(80,255,120),1,cv2.LINE_AA)
                ok,jpg=cv2.imencode('.jpg',rgb)
                if not ok:raise RuntimeError('Detection JPEG encoding failed')
                with self.lock:
                    if generation!=self.generation:continue
                    self.result={'status':'READY','frame_id':frame_id,'captured':captured,
                    'result_age_at_finish_ms':(time.monotonic()-captured)*1000,'inference_ms':(time.monotonic()-started)*1000,
                    'model':'YOLO11n / COCO80 / ONNX '+self.provider,'provider':self.provider,
                    'model_sha256':model_sha,'objects':objects,'jpeg':jpg.tobytes()}
                    self.frames.put(frame_id,captured,self.result['jpeg'])
        except Exception as e:
            with self.lock:self.status='FAULT: '+str(e);self.result=None;self.frames.clear()
