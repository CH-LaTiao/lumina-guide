"""Aggregate current diagnostics into operator-visible readiness gates."""


def _field(key,label,unit,minimum,maximum):
    return {'key':key,'label':label,'unit':unit,'min':minimum,'max':maximum,'value':None}


def next_actions(stage,checks):
    actions=[];perception_missing=[
        label for key,label in (('camera_fresh','相机画面'),('aligned_depth','RGB-D对齐'),
            ('cuda_detection','CUDA检测')) if not checks[key]['ok']]
    if perception_missing:
        actions.append({'id':'restore_perception','title':'恢复感知链路',
            'detail':'检查相机连接、RGB-D对齐和CUDA检测；'+'、'.join(perception_missing)+'未通过。',
            'requires_user_measurement':False,'fields':[]})
    if not checks['imu_gait']['ok']:
        actions.append({'id':'restore_imu_gait','title':'恢复或标定IMU步态',
            'detail':checks['imu_gait']['detail']+'；自动模式会暂用站立策略，不能把普通晃动当作步行。',
            'requires_user_measurement':False,'fields':[]})
    if not checks['ground_confirmed']['ok']:
        actions.append({'id':'apply_bench_reference','title':'应用静止台架地面参考',
            'detail':'用卷尺和角度尺实测镜头离地高度与向下俯角，保持相机/背心固定，再等待地面连续匹配。不得使用估计值。',
            'requires_user_measurement':True,
            'fields':[_field('height_m','镜头离地高度','m',.2,2.5),
                _field('pitch_down_deg','向下俯角','deg',-20,60)],
            'endpoint':'/geometry/reference','guide':'v0.7.35身体标定流程.md'})
    elif stage in ('BODY_CALIBRATION','BODY_ENVELOPE_AND_MAPPING') or not (
            checks['body_calibration']['ok'] and checks['human_width']['ok'] and checks['body_direction']['ok']):
        actions.append({'id':'apply_body_calibration','title':'完成佩戴安装与人体净宽测量',
            'detail':'测量镜头相对身体中心的前移/右移、相机偏航和含白手杖最小净宽；保持安装不变并等待身体方向映射无歧义。',
            'requires_user_measurement':True,
            'fields':[_field('camera_forward_m','镜头前移','m',-.5,.5),
                _field('camera_right_m','镜头右移','m',-.5,.5),
                _field('yaw_deg','相机偏航','deg',-45,45),
                _field('required_passage_width_m','含白手杖最小净宽','m',.35,1.5)],
            'endpoint':'/body/calibration','command':
                'python3 tools/calibrate_mount.py --confirm-measured --height-m <实测> --pitch-down-deg <实测> --camera-forward-m <实测> --camera-right-m <实测> --yaw-deg <实测> --required-width-m <实测>',
            'guide':'v0.7.35身体标定流程.md'})
    if not checks['physical_output_off']['ok']:
        actions.append({'id':'restore_physical_output_off','title':'关闭物理输出',
            'detail':'验证结束前保持振动和实体播报关闭，避免诊断结果触发真实设备。',
            'requires_user_measurement':False,'fields':[]})
    if not checks['audio_route']['ok']:
        actions.append({'id':'restore_audio_route','title':'恢复NoMachine音频路由',
            'detail':'核对输入源、输出设备和远端monitor转发；路由恢复不代表语音可懂度已验收。',
            'requires_user_measurement':False,'fields':[]})
    if stage=='FIELD_VALIDATION':
        actions.append({'id':'run_field_validation','title':'执行受控场地佩戴行走验收',
            'detail':'完成开阔、中央障碍、窄缝、侧障、落差和动态近物路线；记录轨迹并审计版本、动作、短句和移动门控。',
            'requires_user_measurement':False,'fields':[],
            'command':'python3 tools/route_trace.py --seconds 60 --label controlled --output runtime/controlled.jsonl && python3 tools/trace_report.py runtime/controlled.jsonl --output runtime/controlled-report.json',
            'guide':'v0.7.33受控轨迹记录.md'})
    return actions


def assess(info):
    geometry=info.get('geometry') or {}
    analysis=info.get('guidance_analysis') or {}
    guidance=info.get('guidance') or {}
    detection=info.get('detection') or {}
    audio=info.get('audio') or {}
    online=info.get('online') or {}
    imu=info.get('imu') or {}
    body=geometry.get('body_calibration') or {}
    body_active=isinstance(body,dict) and body.get('state')=='ACTIVE'
    ground=geometry.get('ground_state')=='REFERENCE_MATCHED_CANDIDATE'
    fresh_limit=200 if guidance.get('mode')=='WALKING' else 500
    fresh=(info.get('state')=='STREAMING' and isinstance(info.get('frame_age_ms'),(int,float))
           and 0<=info['frame_age_ms']<=fresh_limit)
    cuda=detection.get('status')=='READY' and detection.get('provider')=='CUDA'
    aligned=bool(info.get('aligned'))
    direction_ready=(analysis.get('direction_frame')=='BODY' and analysis.get('calibration_applied')
                     and not analysis.get('native_body_ambiguous'))
    width=body.get('required_passage_width_m') if body_active else None
    human_width=isinstance(width,(int,float)) and width>=.35
    audio_ready=bool((audio.get('route') or {}).get('available'))
    movement_released=guidance.get('movement_allowed') is True
    physical_off=info.get('real_output') is False
    imu_age=imu.get('age_ms')
    imu_sensor_ok=bool(imu.get('accel_valid') and imu.get('gyro_valid')
        and isinstance(imu_age,(int,float)) and 0<=imu_age<=500)
    imu_gait_ok=bool(imu_sensor_ok and imu.get('state') in ('WALKING','STANDING'))
    checks={
        'camera_fresh':{'ok':bool(fresh),'detail':(
            f'相机画面新鲜（{fresh_limit}ms门限）' if fresh else '相机画面不新鲜或未连接')},
        'aligned_depth':{'ok':aligned,'detail':'RGB-D已对齐' if aligned else 'RGB-D未对齐'},
        'cuda_detection':{'ok':bool(cuda),'detail':'YOLO运行在CUDA' if cuda else 'YOLO未在CUDA READY状态'},
        'imu_gait':{'ok':imu_gait_ok,'detail':(
            f"IMU步态 {imu.get('state')}" if imu_gait_ok else
            ('IMU步态尚未稳定' if imu_sensor_ok else 'IMU加速度计/陀螺仪未就绪'))},
        'ground_confirmed':{'ok':bool(ground),'detail':'地面候选已确认' if ground else '地面尚未连续确认'},
        'body_calibration':{'ok':body_active,'detail':'身体安装标定有效' if body_active else '身体安装标定未设置或已过期'},
        'human_width':{'ok':bool(human_width),'detail':f"人体净宽 {width:.2f}m" if human_width else '含白手杖人体净宽未设置'},
        'body_direction':{'ok':bool(direction_ready),'detail':'身体方向映射无歧义' if direction_ready else '身体方向映射未就绪'},
        'audio_route':{'ok':audio_ready,'detail':'NoMachine音频路由可用' if audio_ready else 'NoMachine音频路由不可用'},
        'online_configured':{'ok':bool(online.get('configured')),'detail':'在线问答已配置' if online.get('configured') else '在线问答未配置'},
        'physical_output_off':{'ok':physical_off,'detail':'物理输出关闭' if physical_off else '检测到物理输出开启'},
    }
    blockers=[]
    for key in ('camera_fresh','aligned_depth','cuda_detection','imu_gait','ground_confirmed','body_calibration',
                'human_width','body_direction','audio_route','physical_output_off'):
        if not checks[key]['ok']:blockers.append(checks[key]['detail'])
    if not movement_released:blockers.append('移动许可门控尚未释放')
    blockers.append('受控场地真值和佩戴行走验收尚未完成')
    ready_for_body_guidance=all(checks[key]['ok'] for key in
        ('camera_fresh','aligned_depth','ground_confirmed','body_calibration','human_width','body_direction'))
    ready_for_auto_mode=all(checks[key]['ok'] for key in
        ('camera_fresh','aligned_depth','imu_gait'))
    if not fresh:stage='PERCEPTION'
    elif not ground:stage='GROUND_REFERENCE'
    elif not body_active:stage='BODY_CALIBRATION'
    elif not human_width or not direction_ready:stage='BODY_ENVELOPE_AND_MAPPING'
    else:stage='FIELD_VALIDATION'
    return {'stage':stage,'ready_for_body_guidance':bool(ready_for_body_guidance),
            'ready_for_auto_mode':bool(ready_for_auto_mode),
            'ready_for_movement':False,'movement_released':bool(movement_released),
            'checks':checks,'blockers':blockers,'next_actions':next_actions(stage,checks),
            'note':'诊断就绪不等于真实通行授权；movement_allowed 必须保持 false 直到硬件与场地验收完成。'}
