const $ = id => document.getElementById(id);
let old = '', deturl = null, detFrame = null, detExpiry = null, streaming = false, feedback = null, spokenKey = null, autoHelpState = null, gaitCalibrationState = null, lastGeometryPaint = 0;
let lineEditing=false,lineY=264,lineDraft=null,lineSaving=false,lineServerState='DEFAULT',lineError='';
const stateNames = {STARTING:'正在启动',CONNECTING:'连接相机',RECONNECTING:'相机重连中',WARMING:'检查相机帧',STALE:'画面已过期',STOPPED:'已停止'};
const cameraRegionNames = ['画面左','画面中','画面右'];
const bodyRegionNames = ['身体左','身体中','身体右'];
for (const i of [0,2,4,1,3,5]) {
  const el = document.createElement('div');el.className='motor';el.id='motor-'+i;
  el.textContent='V'+(i+1)+' · '+cameraRegionNames[Math.floor(i/2)];$('motors').append(el);
}
function event(text) {
  if (text!==old) {$('events').textContent=new Date().toLocaleTimeString()+' '+text+'\n'+$('events').textContent.slice(0,2500);old=text;}
}
function cancelSpeech(){if ('speechSynthesis' in window) window.speechSynthesis.cancel();spokenKey=null;}
function groundLineViewRect(){const view=$('view');return view&&!view.hidden?view.getBoundingClientRect():null;}
function groundLineCameraY(event){
  const rect=groundLineViewRect();if(!rect)return null;
  const y=event.clientY-rect.top;
  if(y<0||y>rect.height)return null;
  return Math.max(0,Math.min(480,y*480/rect.height));
}
function drawGroundLine(){
  const view=$('view'),canvas=$('ground-line-canvas');
  if(!view||!canvas)return;
  if(view.hidden){canvas.hidden=true;return;}
  canvas.hidden=false;
  const rect=view.getBoundingClientRect();
  if(rect.width<2||rect.height<2)return;
  const dpr=window.devicePixelRatio||1;
  const width=Math.round(rect.width*dpr),height=Math.round(rect.height*dpr);
  if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}
  const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,rect.width,rect.height);
  const value=lineDraft??lineY,y=Math.max(0,Math.min(480,value))*rect.height/480;
  ctx.fillStyle='rgba(70,200,150,.10)';ctx.fillRect(0,y,rect.width,rect.height-y);
  ctx.strokeStyle=lineEditing?'#ffd166':'#5de0c0';ctx.lineWidth=lineEditing?3:2;
  if(!lineEditing&&lineServerState==='DEFAULT')ctx.setLineDash([7,5]);
  ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(rect.width,y);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle='#fff';ctx.font='12px system-ui';
  ctx.fillText((lineEditing?'拖动调整 · ':'地面线 · ')+'以下为地面',8,Math.max(14,y-8));
  if(lineEditing){ctx.fillStyle='#ffd166';ctx.beginPath();ctx.arc(rect.width/2,y,5,0,Math.PI*2);ctx.fill();}
}
function updateGroundLineState(){
  const node=$('ground-line-state');if(!node)return;
  if(lineError)node.textContent='地面线保存失败：'+lineError;
  else if(lineSaving)node.textContent='正在保存地面线…';
  else if(lineEditing)node.textContent='上下拖动画面，线以下作为地面';
  else if(lineServerState==='INVALIDATED')node.textContent='会话或相机变化，已恢复默认地面线';
  else if(lineServerState==='SET')node.textContent='自定义地面线 · 以下为地面';
  else node.textContent='默认地面线 55% · 以下为地面';
}
function showGroundLine(value){
  lineServerState=value?.state||'DEFAULT';
  if(!lineEditing&&!lineSaving&&typeOfNumber(value?.line_y))lineY=value.line_y;
  updateGroundLineState();drawGroundLine();
}
function typeOfNumber(value){return typeof value==='number'&&Number.isFinite(value);}
async function saveGroundLine(y,clear=false){
  const previous=lineY;
  if(!clear)lineY=y;
  lineSaving=true;lineError='';updateGroundLineState();drawGroundLine();
  try{
    const value=await post('/ground-line',clear?{clear:true}:{y});
    if(typeOfNumber(value.line_y))lineY=value.line_y;
    lineServerState=value.state||'DEFAULT';
  }catch(e){
    lineY=previous;lineError=e.message;
  }finally{lineSaving=false;updateGroundLineState();drawGroundLine();}
}
$('ground-line-edit').onclick=()=>{
  lineEditing=!lineEditing;
  const canvas=$('ground-line-canvas');canvas.classList.toggle('editing',lineEditing);
  $('ground-line-edit').textContent=lineEditing?'完成调整':'调整地面线';
  updateGroundLineState();drawGroundLine();
};
$('ground-line-reset').onclick=()=>{if(!lineSaving)saveGroundLine(lineY,true);};
const groundLineCanvas=$('ground-line-canvas');
groundLineCanvas.addEventListener('pointerdown',event=>{
  if(!lineEditing||lineSaving)return;
  const y=groundLineCameraY(event);if(y===null)return;
  event.preventDefault();groundLineCanvas.setPointerCapture(event.pointerId);
  lineDraft=y;drawGroundLine();
});
groundLineCanvas.addEventListener('pointermove',event=>{
  if(lineDraft===null)return;
  const y=groundLineCameraY(event);if(y===null)return;
  lineDraft=y;drawGroundLine();
});
groundLineCanvas.addEventListener('pointerup',()=>{
  if(lineDraft===null)return;
  const value=lineDraft;lineDraft=null;saveGroundLine(value);
});
groundLineCanvas.addEventListener('pointercancel',()=>{lineDraft=null;drawGroundLine();});
window.addEventListener('resize',drawGroundLine);
$('view').addEventListener('load',drawGroundLine);
function showFeedback(value) {
  feedback=value;
  const speech=value?.speech;
  const speechKey=speech?String(speech.guidance_event_id??speech.event_id??speech.kind)+':'+speech.text:null;
  const mappingNames={body_direction:'身体方向',body_regions:'身体区域',camera_regions:'相机画面区',none:'暂无振动候选',waiting_for_guidance:'等待引导'};
  $('speech-text').textContent=value?.paused?'模拟已暂停':speech?.text||'等待感知。';
  $('feedback-state').textContent=value?.log_error?'模拟事件写入失败，请检查存储':speech?'优先级 '+speech.priority+' / '+(mappingNames[speech.mapping]||'语音提示'):'跟随实时引导 / 等待稳定提示';
  $('pause-feedback').textContent=value?.paused?'恢复模拟':'暂停模拟';
  $('listen').disabled=!speech||value?.paused||!('speechSynthesis' in window);
  const regionNames=String(speech?.mapping||value?.mapping||'').startsWith('body_')?bodyRegionNames:cameraRegionNames;
  for (let i=0;i<6;i++) {
    const motor=$('motor-'+i);motor.classList.toggle('active',Boolean(value?.haptics?.[i]?.active));
    const label='V'+(i+1)+' · '+regionNames[Math.floor(i/2)];if(motor.textContent!==label)motor.textContent=label;
  }
  $('feedback-events').textContent=(value?.events||[]).map(e=>'#'+(e.event_id||'?')+' '+e.text).join('\n');
  if (spokenKey && (value?.paused||speechKey!==spokenKey)) cancelSpeech();
}
function hideDetection(){clearTimeout(detExpiry);detFrame=null;$('detview').hidden=true;if(deturl){URL.revokeObjectURL(deturl);deturl=null;}detTable.hidden=true;}
const detTable=document.createElement('table');
detTable.id='detobjects';detTable.hidden=true;
detTable.innerHTML='<thead><tr><th>类别</th><th>分数</th><th>簇距离</th><th>补丁距离</th></tr></thead><tbody></tbody>';
$('detstatus').after(detTable);
function updateDetectionTable(objs){
  const body=detTable.querySelector('tbody');
  if(!objs.length){detTable.hidden=true;body.replaceChildren();return;}
  body.replaceChildren();
  for(const o of objs.slice(0,6)){
    const tr=document.createElement('tr');
    for(const v of [o.label,Number(o.score).toFixed(2),
      o.cluster_distance_m!=null?Number(o.cluster_distance_m).toFixed(2)+' m':'—',
      o.depth_patch_m!=null?Number(o.depth_patch_m).toFixed(2)+' m':'—']){
      const td=document.createElement('td');td.textContent=v;tr.append(td);
    }
    body.append(tr);
  }
  detTable.hidden=false;
}
function showGeometry(g) {
  const paintAt=performance.now();
  if(!g){$('geometry-state').textContent='等待新鲜画面';$('geometry-camera').textContent='';$('geometry-objects').replaceChildren();lastGeometryPaint=paintAt;return;}
  if(paintAt-lastGeometryPaint<500)return;
  lastGeometryPaint=paintAt;
  const held=g.display_hold===true;
  $('geometry-objects').replaceChildren();
  if(g.state!=='READY'&&!held){$('geometry-state').textContent='几何不可用：'+(g.error||g.state||'等待新鲜画面');$('geometry-camera').textContent='';return;}
  const names={REFERENCE_INVALIDATED_MOTION:'相机移动，台架参考已清除',UNKNOWN:'地面未知',PLANE_UNCONFIRMED:'发现平面，尚未确认是地面',REFERENCE_MATCHED_CANDIDATE:'与台架测量相符的地面候选'};
  const body=g.body_calibration?.state==='ACTIVE'?' · 身体标定剩余 '+Math.ceil(g.body_calibration.expires_in_s)+' 秒':' · 身体标定未启用';
  const refSource=g.reference?.source==='BODY_MOUNT'?' · 身体安装地面追踪':'';
  const head=held?'保持上一有效帧 · '+Math.round(g.display_age_ms||0)+' ms 前':(names[g.ground_state]||g.ground_state);
  $('geometry-state').textContent=head+' · 连续 '+g.stable_frames+' 帧 · '+g.compute_ms.toFixed(1)+' ms · '+g.cluster_count+' 个物体簇'+(g.reference?' · 参考剩余 '+Math.ceil(g.reference.expires_in_s)+' 秒':'')+refSource+body;
  $('geometry-camera').textContent='SDK内参 '+g.camera.width+'×'+g.camera.height+' / fx '+g.camera.fx.toFixed(2)+' / fy '+g.camera.fy.toFixed(2)+' / '+g.camera.distortion_model+' · 有效采样 '+g.counts.valid+'/'+g.counts.sampled+' · 帧 '+g.frame_id;
  const range=v=>v?v.map(x=>x.toFixed(2)).join(' 至 ')+' m':'未知';
  for(const o of g.objects){const tr=document.createElement('tr');for(const value of ['G'+o.cluster_id+' / '+o.sample_points+'点',{LEFT:'相机左',CENTER:'相机中',RIGHT:'相机右'}[o.camera_side],o.depth_z_p20_m.toFixed(2)+' m',range(o.camera_up_range_m),range(o.height_over_plane_m)]){const td=document.createElement('td');td.textContent=value;tr.append(td);}$('geometry-objects').append(tr);}
}
function offline(text) {
  streaming=false;$('view').removeAttribute('src');$('view').hidden=true;$('missing').hidden=false;$('missing').textContent=text;$('badge').textContent=text;
  drawGroundLine();
    for(const id of ['fps','coverage','age','range','rgbq','camerafps'])$(id).textContent='—';
  $('hazards').textContent='感知不可用';$('regions').replaceChildren();hideDetection();showGeometry(null);$('detector').textContent='不可用';event(text);
}
$('view').onerror=()=>{streaming=false;};
async function tick() {
  try {
    const r=await fetch('/status',{signal:AbortSignal.timeout(2000)});if(!r.ok)throw Error('状态请求失败');
    const s=await r.json();showFeedback(s.feedback);showFunctional(s);showReadiness(s.readiness);showGroundLine(s.ground_line);
    const q=s.online,timing=(q.budget_s?' · 预算'+q.budget_s+'秒':'')+(q.elapsed_ms?' · 用时'+Math.round(q.elapsed_ms)+'ms':'');
    if(typeof refreshPrompts==='function'&&q.prompt_trial&&q.request_id&&q.state!=='RUNNING'&&q.request_id!==lastPromptTrial){lastPromptTrial=q.request_id;refreshPrompts(q.request_id);}
    const round=(q.task&&q.task.rounds?' · 任务第'+q.task.rounds+'/'+q.task.max_rounds+'轮':'');
    const flags=(q.safety_flags&&q.safety_flags.length?' · 安全提示'+q.safety_flags.length+'项':'');
    $('query-state').textContent=q.configured?((q.prompt_trial?'候选试答 · ':q.review?'审阅 · ':'')+(q.note? q.state+' · '+q.note:q.state)+round+flags+timing+(q.prompt_version?' · 提示词 '+q.prompt_version.slice(0,16):'')):'在线密钥未配置；本地感知仍可运行';
    $('ask').disabled=q.busy||!q.configured||s.state!=='STREAMING';
    $('answer').textContent=q.plan?JSON.stringify(q.plan,null,2):(q.error||'');
    if(s.state!=='STREAMING'){
      const message=(stateNames[s.state]||s.state)+(s.retry_seconds?' · '+s.retry_seconds+'秒后重试':'');offline(message);
      $('missing').textContent=message+(s.error?' / '+(s.error==='Configured camera not found'?'未发现指定的336L，请检查USB连接':s.error):'');return;
    }
    if(!streaming){$('view').src='/stream.mjpg?t='+Date.now();streaming=true;}
    $('view').hidden=false;$('missing').hidden=true;$('badge').textContent='实时采集中';drawGroundLine();
    $('fps').textContent=s.capture_pairs_per_s.toFixed(1);$('coverage').textContent=(s.valid_depth_ratio*100).toFixed(1)+'%';$('age').textContent=s.frame_age_ms;
    $('camerafps').textContent=s.camera_fps!=null?s.camera_fps.toFixed(1):'—';
    $('range').textContent=s.range_center_m!=null?s.range_center_m.toFixed(2):'—';
    $('rgbq').textContent=({OK:'正常',OVEREXPOSED:'过曝',LOW_LIGHT:'过暗',MOSTLY_DARK:'偏暗',UNAVAILABLE:'无画面'})[(s.rgb_quality||{}).state]||'—';
    $('regions').replaceChildren();
    s.regions.forEach((row,i)=>{
      const tr=document.createElement('tr');const level=s.native_depth[i].level;
      for (const value of [cameraRegionNames[i],(row.valid_ratio*100).toFixed(1)+'%',row.depth_p10_m===null?'未知':row.depth_p10_m.toFixed(2)+' m',['未知','未发现近物证据','近物候选','较近物体候选'][level]]) {
        const td=document.createElement('td');td.textContent=value;tr.append(td);
      }
      $('regions').append(tr);
    });
    $('hazards').textContent='C++深度处理 '+s.native_depth_ms.toFixed(2)+' ms · 会话 '+s.session_id;
    showGeometry(s.geometry_display||s.geometry);
    event('实时采集中 / '+s.device+' / '+s.session_id);
    const d=s.detection;$('detector').textContent=d.status==='READY'?d.objects.length+'个候选':d.status;
    $('provider').textContent=d.provider?'ONNX Runtime / '+d.provider:'等待检测后端';
    if(d.status==='READY'&&d.frame_id) {
      if(d.frame_id!==detFrame) {
        const requestedAt=performance.now();
        const dr=await fetch('/detection.jpg?frame_id='+encodeURIComponent(d.frame_id),{signal:AbortSignal.timeout(2000)});
        if(dr.ok) {
          if(dr.headers.get('X-Lumina-Frame-Id')!==d.frame_id)throw Error('检测图片帧号不匹配');
          const ageHeader=dr.headers.get('X-Lumina-Frame-Age-Ms');
          const age=Number(ageHeader);const blob=await dr.blob();
          const remaining=2000-age-(performance.now()-requestedAt);
          if(ageHeader===null||!Number.isFinite(age)||age<0||remaining<=0){hideDetection();return;}
          const next=URL.createObjectURL(blob);$('detview').src=next;if(deturl)URL.revokeObjectURL(deturl);
          deturl=next;detFrame=d.frame_id;$('detview').hidden=false;
          clearTimeout(detExpiry);detExpiry=setTimeout(hideDetection,remaining);
          $('detstatus').textContent=d.model+' · '+d.inference_ms.toFixed(0)+' ms · 帧 '+d.frame_id;
        } else hideDetection();
        updateDetectionTable(d.objects||[]);
      }
    } else {hideDetection();updateDetectionTable([]);$('detstatus').textContent=d.status;}
  } catch(e) {
    offline('连接中断 / '+e.message);showFeedback(null);cancelSpeech();$('ask').disabled=true;
  } finally {setTimeout(tick,100);}
}
async function post(path,body) {
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(5000)});
  const value=await r.json();if(!r.ok)throw Error(value.error||'请求失败');return value;
}
$('ask').onclick=async()=>{
  if(!$('consent').checked){$('query-state').textContent='请先确认本次图片上传';return;}
  try{await post('/query',{question:$('question').value,scene:$('scene').value,consent:true,review:$('review').checked});$('consent').checked=false;}
  catch(e){$('query-state').textContent=e.message;}
};
$('cancel').onclick=async()=>{try{await post('/cancel',{});}catch(e){event(e.message);}};
$('newtask').onclick=async()=>{try{await post('/task/reset',{});$('query-state').textContent='已开始新任务';}catch(e){event(e.message);}};
$('bench-set').onclick=async()=>{try{
  if(!$('bench-height').value||!$('bench-pitch').value)throw Error('请填写实测高度和俯角');
  await post('/geometry/reference',{height_m:Number($('bench-height').value),pitch_down_deg:Number($('bench-pitch').value),measured:$('bench-confirm').checked,stationary:$('bench-confirm').checked});
  $('bench-confirm').checked=false;$('bench-result').textContent='已应用当前连接的120秒参考，等待连续5帧拟合稳定。';
}catch(e){$('bench-result').textContent=e.message;}};
$('bench-clear').onclick=async()=>{try{await post('/geometry/reference',{clear:true});$('bench-result').textContent='已清除台架参考。';}catch(e){$('bench-result').textContent=e.message;}};
$('body-set').onclick=async()=>{try{
  if(!$('body-forward').value||!$('body-right').value||!$('body-yaw').value||!$('body-width').value)throw Error('请填写前移、右移、偏航角和含手杖净宽，零也必须显式填写');
  await post('/body/calibration',{camera_forward_m:Number($('body-forward').value),camera_right_m:Number($('body-right').value),yaw_deg:Number($('body-yaw').value),required_passage_width_m:Number($('body-width').value),measured:$('body-confirm').checked,stationary:$('body-confirm').checked});
  $('body-confirm').checked=false;$('body-result').textContent='已应用30分钟身体标定；需要当前地面候选持续匹配，方向才会切换为身体坐标。';
}catch(e){$('body-result').textContent=e.message;}};
$('body-clear').onclick=async()=>{try{await post('/body/calibration',{clear:true});$('body-result').textContent='已清除身体标定，方向退回相机坐标。';}catch(e){$('body-result').textContent=e.message;}};
$('pause-feedback').onclick=async()=>{cancelSpeech();try{showFeedback(await post('/feedback/pause',{paused:!feedback?.paused}));}catch(e){event(e.message);}};
$('listen').onclick=async()=>{
  cancelSpeech();
  try{
    const r=await fetch('/status',{signal:AbortSignal.timeout(1000)});const s=await r.json();const f=s.feedback;
    if(!f?.speech||f.paused)return;
    const text=new SpeechSynthesisUtterance(f.speech.text);text.lang='zh-CN';spokenKey=String(f.speech.guidance_event_id??f.speech.event_id??f.speech.kind)+':'+f.speech.text;
    text.onerror=()=>event('电脑语音不可用，请检查浏览器语音支持');window.speechSynthesis.speak(text);
  }catch(e){event('试听失败：'+e.message);}
};
async function reviewStatus(prefix='') {
  try{const r=await fetch('/reviews/status');const s=await r.json();$('review-state').textContent=prefix+'问答 '+s.queries+' 条 / 人工修订 '+s.revisions+' 版 / 提示词修订 '+s.prompt_candidates+' 条';}
  catch(e){$('review-state').textContent='记录读取失败';}
}
$('import-review').onclick=async()=>{
  const f=$('review-file').files[0];if(!f){$('review-state').textContent='请先选择修订后的CSV';return;}
  if(f.size>2*1024*1024){$('review-state').textContent='单次导入上限2MiB，请分批导入';return;}
  $('import-review').disabled=true;
  try{const r=await fetch('/reviews/import',{method:'POST',headers:{'Content-Type':'text/csv; charset=utf-8'},body:await f.arrayBuffer(),signal:AbortSignal.timeout(10000)});
    const s=await r.json();if(!r.ok)throw Error(s.error);await reviewStatus('本次新增 '+s.imported+' 版，跳过 '+s.skipped+' 条。');}
  catch(e){$('review-state').textContent='导入失败：'+e.message;}
  finally{$('import-review').disabled=false;}
};
window.addEventListener('pagehide',cancelSpeech);reviewStatus();tick();

let voiceState={},guideState={};
function showReadiness(r){
  if(!r){$('readiness-stage').textContent='等待就绪检查';$('readiness-checks').replaceChildren();$('readiness-blockers').textContent='等待门控检查。';$('readiness-actions').replaceChildren();return;}
  const stages={PERCEPTION:'感知未就绪',GROUND_REFERENCE:'等待地面确认',BODY_CALIBRATION:'等待身体安装标定',BODY_ENVELOPE_AND_MAPPING:'等待人体净宽与身体方向',FIELD_VALIDATION:'软件门控就绪，等待场地验收'};
  const names={camera_fresh:'画面',aligned_depth:'RGB-D',cuda_detection:'CUDA',imu_gait:'IMU步态',ground_confirmed:'地面',body_calibration:'身体标定',human_width:'人体净宽',body_direction:'身体方向',audio_route:'音频',online_configured:'在线问答',physical_output_off:'物理输出'};
  $('readiness-stage').textContent=(stages[r.stage]||r.stage||'未知')+' · '+(r.ready_for_body_guidance?'身体方向候选可用':'身体方向候选不可用')+' · 移动授权关闭';
  const box=$('readiness-checks');box.replaceChildren();
  for(const [key,value] of Object.entries(r.checks||{})){
    const tag=document.createElement('span');tag.className='tag';tag.textContent=(value.ok?'✓ ':'× ')+(names[key]||key);tag.title=value.detail||'';box.append(tag);
  }
  $('readiness-blockers').textContent='未通过：'+(r.blockers||[]).join('；');
  const actions=$('readiness-actions');actions.replaceChildren();
  for(const action of r.next_actions||[]){
    const item=document.createElement('li');item.style.margin='8px 0';
    const title=document.createElement('strong');title.textContent=action.title||action.id;item.append(title);
    const detail=document.createElement('div');detail.textContent=action.detail||'';item.append(detail);
    if(action.fields?.length){
      const fields=document.createElement('div');
      fields.textContent='所需实测：'+action.fields.map(f=>`${f.label} / ${f.unit}（${f.min}…${f.max}，当前未填）`).join('；');
      item.append(fields);
    }
    if(action.command){const command=document.createElement('code');command.textContent=action.command;item.append(command);}
    actions.append(item);
  }
}
function showFunctional(s){
  guideState=s.guidance||{};voiceState=s.audio||{};
  autoHelpState=s.auto_help||{};
  gaitCalibrationState=s.gait_calibration||{};
  $('software').textContent='LUMINA / '+(s.software_version||'?')+' / '+(s.software?.commit||'未知提交').slice(0,8)+(s.software?.dirty?' · 有未提交修改':'');
  const names={STANDING:'站立观察：可详细提问',WALKING:'步行提示：短句优先',PAUSED:'提示已暂停'};
  const requested={AUTO:'自动适应',STANDING:'手动站立',WALKING:'手动步行',PAUSED:'暂停'};
  const gait=guideState.gait?.state||guideState.policy?.gait_state||'UNKNOWN';
  $('guidance-mode').textContent=(requested[guideState.requested_mode]||'等待模式')+' · 当前 '+(names[guideState.mode]||guideState.mode||'?')+' · IMU '+gait;
  const quiet={STANDING:'安静观察，按需提问。',WALKING:'等待稳定方向。',PAUSED:'提示已暂停。'};
  $('guidance-speech').textContent=guideState.speech||quiet[guideState.mode]||'等待模式状态。';
  $('guidance-summary').textContent=(guideState.mode==='STANDING'&&guideState.summary)?'方向摘要：'+guideState.summary:'';
  const route=guideState.mode==='WALKING'&&guideState.route?' / 路线 '+(guideState.route.kind||'?')+(guideState.route.clear_width_m==null?'':' '+guideState.route.clear_width_m+' m'):'';
  const risk=guideState.motion_risk?' / 接近 '+guideState.motion_risk.closing_speed_m_s+' m/s · TTC '+guideState.motion_risk.ttc_to_clearance_s+' s':'';
  const transition=guideState.ground_transition?' / 地面过渡 '+guideState.ground_transition+(guideState.ground_transition_height_m==null?'':' '+guideState.ground_transition_height_m+' m'):'';
  const basis=guideState.direction_frame==='BODY'?'身体坐标':'相机坐标';
  const traffic={GO:'可继续',TURN:'需改向',STOP:'停止'}[guideState.traffic_decision]||'待确认';
  $('direction-heading').textContent=basis==='身体坐标'?'身体方向':'相机方向';
  $('guidance-detail').textContent=traffic+' / 状态 '+(guideState.action||'?')+' / '+basis+' / '+guideState.mode_source+' / IMU '+gait+route+risk+transition+' / '+(guideState.reasons||[]).join('、');
  $('corridors').replaceChildren();
  for(const c of guideState.corridors||[]){
    const tr=document.createElement('tr');
    for(const v of [{LEFT:'左',CENTER:'中',RIGHT:'右'}[c.direction],{BLOCKED:'受阻',UNKNOWN:'未知',OBSERVED_CLEAR_CANDIDATE:'空间候选，待确认'}[c.state],c.ground_cells+'/'+c.required_ground_cells,c.nearest_obstacle_m==null?'—':c.nearest_obstacle_m+' m',{LOW:'低矮',BODY:'人体高',OVERHEAD:'上方'}[c.obstacle_class]||'—']){
      const td=document.createElement('td');td.textContent=v;tr.append(td);
    }$('corridors').append(tr);
  }
  $('audio-state').textContent=(voiceState.route?.available?'NoMachine音频路由可用':'NoMachine音频路由不可用')+' / '+(voiceState.state||'?')+' / '+(voiceState.tts_state||'未播报')+' / 短句缓存 '+(voiceState.cache_phrases||0);
  $('audio-transcript').textContent=voiceState.transcript||voiceState.error||'';
  $('audio-auto').textContent=voiceState.auto?'关闭固定提示自动播报':'开启固定提示自动播报';
  $('audio-record').disabled=!voiceState.route?.available||['RECORDING','TRANSCRIBING'].includes(voiceState.state)||guideState.mode!=='STANDING';
  $('audio-answer').disabled=guideState.mode!=='STANDING'||s.online?.state!=='CANDIDATE'||Boolean(s.online?.safety_flags?.length)||s.online?.gate==='BLOCKED_MOVEMENT';
  $('auto-help-toggle').textContent=autoHelpState.enabled?'关闭自动求助':'开启自动求助';
  $('auto-help-state').textContent=autoHelpState.enabled
    ? '自动求助已开启 · 冷却 '+(autoHelpState.cooldown_s||30)+' 秒 · 近5分钟 '+(autoHelpState.requests_5min||0)+' 次'+(autoHelpState.trigger?' · 最近触发 '+autoHelpState.trigger:'')
    : '自动求助关闭';
  const gc=gaitCalibrationState;
  const gcNames={IDLE:'等待开始',COLLECTING_STILL:'静止采样中',STILL_READY:'静止采样完成，可开始步行采样',COLLECTING_WALK:'步行采样中',COMPLETE:'步态标定完成并已热加载',ERROR:'步态标定失败'};
  $('gait-result').textContent=(gcNames[gc.state]||gc.state||'等待步态标定')+(gc.remaining_s?' · 剩余 '+gc.remaining_s+' 秒':'')+(gc.error?' · '+gc.error:'')+(gc.profile_ready?' · 已有个人配置':'');
  $('gait-still').disabled=gc.state==='COLLECTING_STILL'||gc.state==='COLLECTING_WALK';
  $('gait-walk').disabled=gc.state!=='STILL_READY';
}
async function audioAction(path,payload={}){
  try{await post(path,payload);$('audio-message').textContent='操作已提交。';}
  catch(e){$('audio-message').textContent=e.message;}
}
for(const [id,mode] of [['mode-auto','AUTO'],['mode-standing','STANDING'],['mode-walking','WALKING'],['mode-paused','PAUSED']]){
  $(id).onclick=()=>audioAction('/guidance/mode',{mode});
}
$('auto-help-toggle').onclick=async()=>{
  const enabled=!Boolean(autoHelpState?.enabled);
  if(enabled&&!$('auto-help-consent').checked){$('auto-help-state').textContent='请先勾选自动上传授权';return;}
  try{
    const value=await post('/online/auto_help',{enabled,consent:$('auto-help-consent').checked});
    autoHelpState=value;$('auto-help-state').textContent=enabled?'自动求助已开启':'自动求助关闭';
  }catch(e){$('auto-help-state').textContent=e.message;}
};
$('gait-still').onclick=async()=>{
  if(!$('gait-confirm').checked){$('gait-result').textContent='请先确认安全场地和助手在场';return;}
  try{gaitCalibrationState=await post('/imu/gait_calibration',{action:'start_still',consent:true,safe_environment:true});}
  catch(e){$('gait-result').textContent=e.message;}
};
$('gait-walk').onclick=async()=>{
  if(!$('gait-confirm').checked){$('gait-result').textContent='请先确认安全场地和助手在场';return;}
  try{gaitCalibrationState=await post('/imu/gait_calibration',{action:'start_walk',consent:true,safe_environment:true});}
  catch(e){$('gait-result').textContent=e.message;}
};
$('gait-reset').onclick=async()=>{try{gaitCalibrationState=await post('/imu/gait_calibration',{action:'reset'});}catch(e){$('gait-result').textContent=e.message;}};
$('audio-test').onclick=()=>audioAction('/audio/test');
$('audio-auto').onclick=()=>audioAction('/audio/auto',{enabled:!voiceState.auto});
$('audio-stop').onclick=()=>audioAction('/audio/cancel');
$('audio-record').onclick=async()=>{
  if(!$('audio-consent').checked){$('audio-message').textContent='请先勾选本次录音上传授权。';return;}
  await audioAction('/audio/record',{consent:true});$('audio-consent').checked=false;
};
$('audio-use').onclick=()=>{if(voiceState.transcript){$('question').value=voiceState.transcript;$('question').focus();}};
$('audio-answer').onclick=()=>audioAction('/audio/answer');
