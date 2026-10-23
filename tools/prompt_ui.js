/* Scene prompt revisions and explicit human assessment. No automatic cloud request. */
let promptData={versions:[],active:[],trials:[],events:[]},lastPromptTrial=null;
const promptNames={general:'一般场景',find_target:'寻找目标',obstacle:'障碍方案',metro:'地铁任务',stairs:'楼梯',traffic_signal:'交通信号',read_sign:'读取标识'};
function selectedPrompt(){return promptData.versions.find(v=>v.version===$('prompt-version').value);}
function promptOptions(id,items,label,keep){
  const select=$(id);select.replaceChildren();
  for(const item of items){const option=document.createElement('option');option.value=item.id;option.textContent=label(item);select.append(option);}
  if(items.some(item=>item.id===keep))select.value=keep;
}
function showPromptVersion(reset=false){
  const version=selectedPrompt();
  $('prompt-preview').textContent=version?(promptNames[version.scene]+' · '+version.version.slice(0,12)+'\n补充规则：\n'+version.supplement+'\n期望回答：\n'+(version.expected_answer||'未填写')):'先导入包含 corrected_prompt 的修订CSV，再建立候选。';
  $('prompt-activate').disabled=!version?.eligible;
  $('prompt-trial').disabled=!version;
  if(reset&&version)$('prompt-question').value=version.question;
}
function showPromptTrial(){
  const trial=promptData.trials.find(t=>t.request_id===$('prompt-result').value);
  const version=promptData.versions.find(v=>v.version===trial?.result.prompt_version);
  $('prompt-answer').textContent=trial?JSON.stringify({question:trial.question,scene:trial.scene,expected_answer:version?.expected_answer||'',
    result:trial.result,assessment:trial.assessment},null,2):'暂无试答；评测结果保存后可在这里选择。';
  for(const id of ['prompt-scene-ok','prompt-answer-ok','prompt-step-ok'])$(id).checked=false;
}
async function refreshPrompts(selectedTrial){
  try{
    const r=await fetch('/prompts/status',{signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error('版本读取失败');
    const version=$('prompt-version').value,trial=selectedTrial||$('prompt-result').value;
    promptData=await r.json();
    promptOptions('prompt-version',promptData.versions.map(v=>({...v,id:v.version})),v=>promptNames[v.scene]+' · '+v.version.slice(0,12)+(v.eligible?' · 已有通过样本':' · 待评测'),version);
    promptOptions('prompt-result',promptData.trials.map(t=>({...t,id:t.request_id})),t=>t.created_utc+' · '+promptNames[t.scene]+' · '+t.result.state+' · '+t.request_id.slice(0,8),trial);
    showPromptVersion(!version);showPromptTrial();
    $('prompt-active').textContent='当前场景版本：\n'+promptData.active.map(a=>promptNames[a.scene]+'：'+(a.version?a.version.slice(0,12):'内置')+'（变更序号 '+a.generation+'）'+(a.needs_review?' · 需重新评测，暂用内置':'')).join('\n')+
      '\n\n最近变更：\n'+promptData.events.slice(0,5).map(e=>e.created_utc+' '+promptNames[e.scene]+' '+e.kind+' · '+e.note).join('\n');
    $('prompt-state').textContent='候选 '+promptData.versions.length+' 个；显示最近 '+promptData.trials.length+' 条试答。';
  }catch(e){$('prompt-state').textContent=e.message;}
}
async function promptAction(button,action){
  button.disabled=true;
  try{await action();}catch(e){$('prompt-state').textContent=e.message;}
  finally{button.disabled=false;showPromptVersion();}
}
$('prompt-version').onchange=()=>showPromptVersion(true);
$('prompt-result').onchange=showPromptTrial;
$('prompt-refresh').onclick=()=>refreshPrompts();
$('prompt-sync').onclick=()=>promptAction($('prompt-sync'),async()=>{await post('/prompts/sync',{});await refreshPrompts();});
$('prompt-trial').onclick=()=>promptAction($('prompt-trial'),async()=>{
  const version=selectedPrompt();if(!version)throw Error('请选择候选');
  if(!$('prompt-consent').checked)throw Error('请勾选本次试答上传授权');
  const result=await post('/prompts/trial',{version:version.version,question:$('prompt-question').value,consent:true});
  $('prompt-consent').checked=false;$('prompt-state').textContent='试答进行中：'+result.request_id.slice(0,8)+'，完成后将显示在下方。';
  // Poll stored evidence as well as the live answer; timeout/cancel may finish without a live candidate.
  let polls=0;
  const poll=async()=>{if(++polls>18)return;await refreshPrompts(result.request_id);
    if(!promptData.trials.some(t=>t.request_id===result.request_id))setTimeout(poll,2000);};
  setTimeout(poll,2000);
});
async function assessPrompt(accepted){
  const request=$('prompt-result').value;if(!request)throw Error('请选择已完成的试答');
  await post('/prompts/assess',{request_id:request,accepted,note:$('prompt-note').value,
    checks:{scene_correct:$('prompt-scene-ok').checked,answers_question:$('prompt-answer-ok').checked,next_step_useful:$('prompt-step-ok').checked}});
  await refreshPrompts(request);
}
$('prompt-pass').onclick=()=>promptAction($('prompt-pass'),()=>assessPrompt(true));
$('prompt-fail').onclick=()=>promptAction($('prompt-fail'),()=>assessPrompt(false));
async function changePrompt(action){
  const version=selectedPrompt();if(!version)throw Error('请选择要操作的场景候选');
  const active=promptData.active.find(a=>a.scene===version.scene);
  await post(action==='rollback'?'/prompts/rollback':'/prompts/activate',{
    scene:version.scene,version:action==='default'?null:version.version,generation:active.generation,note:$('prompt-note').value});
  await refreshPrompts();
}
for(const action of ['activate','rollback','default'])$('prompt-'+action).onclick=()=>promptAction($('prompt-'+action),()=>changePrompt(action));
refreshPrompts();
