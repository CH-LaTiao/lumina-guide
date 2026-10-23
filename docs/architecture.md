# 架构与进程模型

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
