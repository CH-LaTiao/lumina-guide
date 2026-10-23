"""Link YOLO boxes to same-frame 3D clusters for a surface distance.

The detector's centre-patch median can mix background pixels. When a geometry
cluster overlaps the box in exactly the same session and frame, its depth
percentile is the better distance estimate; otherwise the patch value stands.
"""


def iou(a,b):
    ax1,ay1,ax2,ay2=a;bx1,by1,bx2,by2=b
    ix1=max(ax1,bx1);iy1=max(ay1,by1);ix2=min(ax2,bx2);iy2=min(ay2,by2)
    inter=max(0,ix2-ix1)*max(0,iy2-iy1)
    area_a=max(0,ax2-ax1)*max(0,ay2-ay1)
    area_b=max(0,bx2-bx1)*max(0,by2-by1)
    union=area_a+area_b-inter
    return inter/union if union>0 else 0.0


def frame_index(frame_id):
    try:return int(str(frame_id).rsplit(':',1)[1])
    except (ValueError,IndexError,AttributeError):return None


def link(objects,clusters,detection_frame=None,geometry_frame=None,min_iou=.25,max_gap=0):
    detection_index=frame_index(detection_frame)
    geometry_index=frame_index(geometry_frame)
    if (detection_index is None or geometry_index is None
            or detection_frame != geometry_frame):
        return [dict(obj,depth_source='patch') for obj in objects]
    linked=[]
    for obj in objects:
        best=None;best_iou=0.0
        box=obj.get('bbox') or [0,0,0,0]
        for cluster in clusters:
            score=iou(box,cluster.get('bbox') or [0,0,0,0])
            if score>best_iou:best=cluster;best_iou=score
        enriched=dict(obj,depth_source='patch')
        if best is not None and best_iou>=min_iou and best.get('depth_z_p20_m') is not None:
            enriched.update(depth_source='cluster',cluster_id=best.get('cluster_id'),
                cluster_distance_m=best.get('depth_z_p20_m'),cluster_iou=round(best_iou,3))
        linked.append(enriched)
    return linked
