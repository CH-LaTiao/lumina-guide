# 架构与进程模型

![系统数据流动画](assets/system-overview.gif)

动画按顺序演示模块间的数据交接，不表示真实运行速率。在线分支仅在获得授权后启用。[静态版本](assets/system-overview.svg)。

## 数据流

```text
Gemini 336L
  -> C++ frame-server (exclusive camera owner)
  -> POSIX shared memory (3 slots, seqlock, RGB-D + IMU)
  -> Python live view
       -> scene analysis / YOLO / geometry
       -> standing or walking guidance
       -> local Piper speech
       -> six-channel virtual haptics
       -> HTTP dashboard and optional online review
```

## 进程

- **frame-server**：独占相机，异步对齐 RGB-D，发布共享内存；同时发布 IMU 快照。
- **live-view**：读取共享内存，完成检测、几何、引导、页面和音频。
- **watchdog**：可选的手动启动守护；本仓库不自动安装开机自启。
- **online worker**：仅在用户授权后创建；有独立预算、取消和迟到结果丢弃。

## 安全状态

所有输出都带来源、新鲜度、时间戳和阻断状态。相机过期、共享内存停流、决策线程停滞或身体标定失效时，系统撤回候选路线并保持移动许可关闭。

## 地面线

地面线是一条水平分界：

- `y >= line_y`：地面候选；
- `y < line_y`：不参与地面拟合；
- 障碍聚类不因地面线而跳过。

地面线绑定相机会话和内参哈希，默认 55%，可调 20%–95%。

## 从输入到输出的一次处理

1. 相机采集彩色、深度及运动信息，由帧服务发布共享内存。
2. 消费者核对帧标识、时间戳与会话，获取可用于当前分析的数据。
3. 目标检测提供类别线索，深度与几何处理提供空间证据。检测结果必须关联自己的输入帧。
4. 引导策略根据站立 / 步行状态、风险与连续帧证据形成候选提示；过期方向被撤回。
5. 语音、虚拟振动和可视化共享引导状态，避免各自对同一场景作出不一致判断。
6. 用户提出场景问题并授权快照时，在线审阅路径构造提示词并校验答案；该分支不能直接授予移动许可。

## 调试、评测与版本管理

| 工具类别 | 公开仓库入口 | 用途 |
| :--- | :--- | :--- |
| 运行与可视化 | `tools/live_view.py`、`tools/status_console.py` | 查看图像、后端、帧龄、引导和反馈 |
| 场景与提示词 | `tools/scene_router.py`、`tools/prompt_registry.py` | 场景选择、提示词版本与人工评测 |
| 回归与算法验证 | `tests/`、`tools/guidance_benchmark.py` | 验证确定性逻辑与接口行为 |
| 现场诊断与追踪 | `tools/verify_runtime.py`、`tools/route_trace.py` | 运行采样、轨迹记录与问题复查 |
| 发布与恢复 | `tools/releases.py`、`tools/backup_runtime.py` | 版本归档、回滚与运行数据备份 |

测试工具的存在不等于所有使用场景均已通过。实体输出、真实佩戴、距离真值与场地验收仍按公开说明分别验证。
