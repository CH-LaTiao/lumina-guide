"""Explicit billable synthetic-image smoke test; no camera image is read or uploaded."""
import argparse,json,os,time,uuid
import cv2
import numpy as np
from online_query import OnlineQuery,ROOT,ACTION_BUDGET_S,REVIEW_BUDGET_S
p=argparse.ArgumentParser();p.add_argument('--key-stdin',action='store_true')
p.add_argument('--review',action='store_true',help='use the explicit 30s human-review budget')
a=p.parse_args()
if a.key_stdin:os.environ['DASHSCOPE_API_KEY']=input().strip()
image=np.full((256,256,3),255,np.uint8);cv2.rectangle(image,(64,64),(192,192),(0,0,255),-1)
ok,data=cv2.imencode('.jpg',image)
if not ok:raise SystemExit('Synthetic image encode failed')
q=OnlineQuery(ROOT/'runtime/synthetic-query.sqlite3');q.generation=1
budget=REVIEW_BUDGET_S if a.review else ACTION_BUDGET_S
q.run(str(uuid.uuid4()),1,'描述图像里的颜色和形状。这是合成测试图，不是实际道路。','general',
      {'captured':time.monotonic(),'jpeg':data.tobytes(),'frame_id':0},
      review=a.review,budget=budget)
result=q.read();print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(0 if result['state']=='CANDIDATE' else 1)
