# 公开更新与验证记录

## v0.11.3 · 2026-10-27

本次仅同步公开演示实现与相应回归。保留 [公开与商业版本说明](../NOTICE.zh-CN.md)，不包含私人开发历史、运行资料、模型权重或设备配置。

| 改动 | 当前行为 | 回归覆盖 |
| :--- | :--- | :--- |
| 站立近墙提示 | 比较有效深度与障碍证据；右侧近、左侧空间更充足时提示偏左 | 原始问题的合成数值场景、左右镜像、低覆盖和身体映射不确定场景 |
| 前方近物 | 已排除的中间区域不能通过兜底重新成为“走中”；没有合适候选时提示停 | 中间近物、两侧受阻、危险像素与旧方向立即撤回 |
| 感知与显示 | 感知独立发布；预览保留自己的帧标识和时间，画面编码不阻塞感知发布 | 慢编码、编码失败、旧预览、新感知及停流处理 |
| 语音时效 | 排队与播放过程中检查当前证据；模式切换和过期撤回旧提示 | 合成延迟、播放中失效、优先级打断与暂停 |
| 在线任务 | “新问题”创建独立任务；“继续当前任务”保留目标并使用新快照；试答与正式任务分开 | 同场景换目标、继续追问、轮次与时效限制、取消和试答隔离 |
| 公开配置 | 专属服务地址改由 `LUMINA_API_HOST` 提供；API Key 只从进程环境或标准输入读取 | HTTPS 地址校验、拒绝地址内凭据、路径和查询参数 |
| 发布检查 | 检查工作区、Git 暂存内容与历史凭据模式，输出位置和类别，不输出命中值 | 暂存和工作区不一致、已删除历史凭据、私有文件与占位符 |

### 本次实际检查

- 108 项无硬件 Python 回归通过；在线请求与音频播放使用隔离的模拟适配器，没有使用真实 API Key、上传相机图或播放音频。
- Python 编译、页面 JavaScript 语法与公开内容检查单独执行；CI 使用同一组无硬件回归入口。
- 私有开发仓库与公开仓库分别保留历史；公开提交不包含私有仓库的 `.git`、运行数据库、日志、照片、设备编号及归档。

无硬件回归验证的是确定性逻辑与接口行为，不等于距离真值、真实佩戴或行走安全验收。相机画面左右的“偏左／偏右”仍是观察与调整提示；身体标定及现场条件未经确认时，不能解释为已验证路线。实体输出和移动许可保持原有阻断。

### 复现检查

```bash
python3 -m pip install -r requirements-test.txt
python3 tools/check_public_secrets.py
python3 tools/check_public_secrets.py --staged --history
python3 -m compileall -q tools tests
node --check tools/live_view.js
python3 -m unittest -v tests.test_live_view_js tests.test_ground_line tests.test_public_security tests.test_standing_clearance tests.test_online_tasks tests.test_perception_preview tests.test_voice_validity tests.test_auto_help tests.test_online_query tests.test_guidance_pipeline tests.test_voice_io
```

`--history` 额外检查所有可达历史文件中的凭据模式，不能擦除已发布历史；历史中的一般地址或说明文本需另行人工复核。检查器属于规则扫描，不能覆盖所有密钥格式或替代公开内容审阅。
