<p align="center"><img src="docs/assets/lumina-banner.svg" alt="LUMINA Guide：可穿戴RGB-D輔助原型與背心形態示意" width="100%" /></p>

# LUMINA Guide / 靈犀導盲背心

[![Static checks](https://github.com/CH-LaTiao/lumina-guide/actions/workflows/static-checks.yml/badge.svg)](https://github.com/CH-LaTiao/lumina-guide/actions/workflows/static-checks.yml)

[简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [English](README.en.md)

面向視障人士的可穿戴 RGB-D 場景理解與通行提示原型，運行於 **Jetson Orin Nano + Orbbec Gemini 336L**。

> **公開演示版 / Public demo**  
> 本倉庫僅公開可展示的架構、介面和部分實作。商業化核心版本閉源，關鍵演算法、參數、模型整合、部署設定與現場適配細節未公開，保留技術細節。公開版本不定期更新，不承諾固定週期或完整支援。

> **資助 / Funding**  
> 本專案為香港數碼港（Cyberport Hong Kong）資助專案。資助不代表數碼港對產品功能、安全性、醫療器材合規性或商業可行性作出認證或背書。

> 這是研究與工程驗證原型，不是醫療器材，也不是已經驗收的自主導航設備。預設保持 `movement_allowed=false`。

**本次公開更新：v0.11.3（2026-10-27）**。近牆提示比較實測空間，前方近物時撤回「走中」；感知與畫面編碼分開發布，過期語音會停止，線上問答明確區分新問題與繼續任務。本次同步通過 108 項無硬體回歸，並加入公開內容憑據檢查。詳見 [更新與驗證記錄（簡體）](docs/public-updates.zh-CN.md)。專屬服務地址與密鑰保留在本機環境設定中。

## 目前能力

- **即時感知**：640×480 RGB-D 對齊、共享記憶體三槽發布、新鮮度檢查與相機自動恢復。
- **三維診斷**：地面平面 RANSAC、障礙高度範圍、連續幀穩定度、水平地面線與物體簇。
- **手動地面線**：在即時畫面上下拖動一條水平線；線以下作為地面擬合區域，線以上不參與地面擬合。預設 55%，可調 20%–95%。
- **CUDA 檢測**：YOLO11n ONNX，優先使用 `CUDAExecutionProvider`，介面顯示實際後端與耗時。
- **站立／步行策略**：讀取 336L IMU；自動切換站立觀察與步行短句；轉彎時暫停舊路線確認。
- **通行提示**：結論優先，例如「前方可走」「停，改走左側」「停，有台階」「停，感知不可用」。
- **本地語音**：Piper `zh_CN-huayan-medium` 常駐程序與預合成短句；eSpeak NG 僅作本地備援，不使用雲端 TTS。
- **虛擬震動**：六通道模擬；未完成身體校正時按相機區域顯示，不與真實硬體混用。
- **可選雲端審閱**：經明確授權後提交單次 RGB 圖與問題；結果只作候選，必須通過 Schema、安全規則與人工審閱。
- **提示詞管理**：CSV 修訂、候選版本、授權試答、人工評測、啟用與回滾。
- **統一感知台**：影像、語音震動、目標檢測同屏，三維幾何在下方。

## 架構

![Animated system data flow](docs/assets/system-overview.gif)

*循環動畫展示系統資料流，並非即時設備遙測。[靜態架構圖](docs/assets/system-overview.svg)。*

```text
Gemini 336L
  -> C++ frame-server（獨占相機）
  -> POSIX shared memory（RGB-D + IMU）
  -> Python live view
       -> YOLO / geometry / guidance
       -> local Piper speech
       -> virtual haptics
       -> dashboard and optional cloud review
```

1. C++ 幀服務獨占相機，Python 只讀共享記憶體。
2. 站立模式提供方向摘要；步行模式只輸出一句結論優先短句。
3. 地面線只影響地面擬合，不隱藏障礙。
4. 雲端結果不能直接控制馬達或移動。
5. 感知過期、相機斷線或決策停滯時，舊方向立即撤回。

更多技術細節見 [架構文件](docs/architecture.md) 與 [安全與隱私](docs/safety-and-privacy.md)。

## 模組如何協作

系統以「擷取 → 感知 → 判斷 → 回饋 → 人工複核」組織。深度幾何提供空間證據，目標檢測提供類別線索，IMU 提供運動狀態，再由引導策略產生簡潔提示。複雜場景問題透過獨立、明確授權的線上路徑處理，使本地即時提示不依賴網路回應速度。

| 模組 | 主要職責 | 工程機制 |
| :--- | :--- | :--- |
| 擷取 | 提供 RGB-D 與 IMU 資料 | C++ 獨占相機、共享記憶體發布、幀標識與時間戳 |
| 感知 | 結合物體類別與空間證據 | CUDA 優先的 ONNX 推理、檢測幀關聯、深度幾何與地面擬合 |
| 引導 | 配合站立與步行資訊需求 | 場景摘要、步行短句、連續幀確認、風險優先與過期方向撤回 |
| 回饋 | 保持語音與虛擬震動一致 | 共用引導狀態、本地 Piper 語音與優先級處理 |
| 線上審閱 | 回答已授權快照的場景問題 | 場景提示詞、結構校驗與規則審閱，不直接控制移動 |
| 提示詞迭代 | 累積可追溯的人工經驗 | CSV 修訂、試答、人工評測、啟用與回滾；不宣稱重新訓練模型權重 |

## 工程化建設與後續方向

除了感知流程，專案包含統一診斷介面、幀與會話新鮮度檢查、回歸測試、確定性演算法場景、執行探針、軌跡記錄、版本識別、回滾及執行資料備份工具。這些機制支援跨階段聯調、問題定位與持續迭代。無硬體 CI、桌面診斷與現場驗收分別記錄，不互相取代。

後續驗證重點包括真實佩戴與步行時延、提示可理解性、玻璃與細桿等困難場景、實體震動整合、供電續航及特定場景人工評測。將導航方向與現場感知結合是發展方向；地鐵路線與站內引導尚不列為已完成能力。公開內容範圍仍以 [NOTICE](NOTICE.zh-TW.md) 為準。

## 目錄

```text
core/          C++ 擷取、共享記憶體、深度區域與幾何聚類
prompts/       雲端場景提示詞與 JSON Schema
deployment/    systemd / udev 可選範本
tools/         Jetson 服務、感知、TTS、雲端問答與診斷工具
tests/         Python 單元、介面與回歸測試
docs/          架構、安裝、安全與隱私
```

## 硬體與依賴

已驗證環境：

- Jetson Orin Nano Super，Ubuntu 22.04 / L4T 36.x
- Orbbec Gemini 336L，USB 3.0
- CUDA / ONNX Runtime GPU
- Python 3.10、CMake 3.16+
- OpenCV、NumPy、SciPy、jsonschema
- 獨立 `pyorbbecsdk` 環境
- Piper 中文模型與 eSpeak NG

模型與相機 SDK **不隨本倉庫分發**。YOLO11n / Ultralytics 模型為 AGPL-3.0；Piper、Orbbec SDK 也各有上游授權，請自行確認並遵守。

## 快速啟動

```bash
export LUMINA_SERIAL="YOUR_DEVICE_SERIAL"
export LUMINA_YOLO_MODEL="$HOME/ultralytics/ultralytics/yolo11n.onnx"
export LUMINA_SDK_PYTHON="$HOME/lumina-sdk-env/bin/python"
export LUMINA_FRAME_SOURCE=shm
export LUMINA_ORBBEC_EXTENSIONS="$HOME/orbbec-sdk/<sdk-version>/lib/extensions"

cmake -S . -B build -DLUMINA_ORBBEC_SDK="$HOME/orbbec-sdk/<sdk-dir>"
cmake --build build -j2
python3 tools/preview_control.py start --lan
```

開啟 `http://<JETSON_IP>:8765/`。完整安裝步驟見 [Jetson 安裝](docs/setup-jetson.md)。

雲端問答金鑰只透過標準輸入注入，不寫入 `.env`、資料庫、日誌或 Git：

```bash
python3 tools/preview_control.py start --lan --key-stdin
```

## 測試

```bash
python3 -m unittest tests/test_live_view_js.py tests/test_ground_line.py -v
node --check tools/live_view.js
python3 -m pytest -q tests
ctest --test-dir build --output-on-failure
```

GitHub Actions 只執行無硬體的靜態檢查；Jetson 即時門檻不能由一般 CI 取代。

## 安全邊界與已知限制

- 不是醫療器材；不能替代白手杖、導盲犬、陪同人員或交通判斷。
- 預設不授予移動許可；實體震動板尚未完成硬體驗收。
- 地面線不是障礙遮蔽；障礙仍從完整畫面檢測。
- 玻璃、鏡面、細桿、懸空物體、樓梯真值與室內外地圖仍需專項驗證。
- 尚未完成真實佩戴行走、距離真值、長期穩定性與使用者可用性驗收。
- 雲端模型可能逾時、產生不確定語言或錯誤事實，只能作候選。

## 公開版與商業版

- **公開演示版**：公開可展示的架構、介面、部分工具與測試。
- **商業核心版**：閉源，包含更完整的核心演算法、參數、模型整合、部署設定與現場適配。
- **更新節奏**：公開演示版不定期更新，不承諾固定節奏。
- **資助**：本專案為香港數碼港（Cyberport Hong Kong）資助專案；資助不構成產品認證或安全背書。
- **聯絡**：商業授權、合作與商業版本諮詢：**wzr0411@gmail.com**

詳見[三語版公開與商業說明](NOTICE.zh-TW.md)與[安全策略](SECURITY.zh-TW.md)。

## 授權

本倉庫是公開演示版，尚未宣告開源許可證。新增許可證前，程式碼可公開查看，但不自動授予複製、修改、再分發或商業使用權利。商業核心版本閉源；商業授權請聯絡 **wzr0411@gmail.com**。
