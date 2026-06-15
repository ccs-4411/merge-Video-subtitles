# 字幕合併工具 🎬

快速、免費的影片字幕燒製工具

## 🌐 線上使用（無需安裝）

👉 **https://ccs-4411.github.io/merge-Video-subtitles/**

- 在瀏覽器中直接處理
- 所有處理完全在本地進行
- 無需上傳服務器

## 🚀 本地部署（更快）

如果你想要 **50 倍更快** 的速度，可以在本地電腦運行：

### 前置要求

- Node.js >= 16.0
- FFmpeg（本地安裝）

### 安裝

```bash
# 1. 克隆或下載本項目
git clone https://github.com/ccs-4411/merge-Video-subtitles.git
cd merge-Video-subtitles

# 2. 安裝依賴
npm install

# 3. 啟動服務器
npm start
```

### 訪問

```
http://localhost:3000
```

## 📊 性能對比

| 方案 | 5 分鐘影片 | 30 分鐘影片 | 原理 |
|------|----------|----------|------|
| 🌐 線上版 | 1-2 分鐘 | 5-10 分鐘 | WASM + 瀏覽器 |
| 🚀 本地版 | 10-15 秒 | 1-2 分鐘 | **原生 FFmpeg** |

## 🎯 功能特性

✅ 支持多種影片格式 (MP4, MKV, WebM, etc.)  
✅ 支持 SRT 和 ASS 字幕格式  
✅ 自動 SRT → ASS 轉換  
✅ 字幕大小可調整  
✅ 中文/英文混合字幕支持  
✅ 實時進度顯示  
✅ 完全本地處理（隱私安全）  

## 🔧 配置

編輯 `server.js` 中的參數：

```javascript
// 編碼速度: ultrafast / superfast / fast / medium
-preset ultrafast

// 品質 (0-51): 較小 = 更好品質但檔案更大
-crf 26

// 音訊比特率
-b:a 128k
```

## 📝 使用說明

1. **選擇影片** - 支持 MP4, MKV, WebM, MOV 等
2. **選擇字幕** - SRT 或 ASS 格式
3. **調整大小** - 可選，預設 22px
4. **點擊合併** - 自動下載結果

## 🐛 故障排除

### "FFmpeg not found"
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Windows
# 下載: https://ffmpeg.org/download.html
```

### 字幕亂碼
- 確保 SRT 檔案編碼為 UTF-8
- 移除 BOM (Byte Order Mark)

### 生成檔案過大
- 降低 `-crf` 值（建議 28-30）
- 使用 `-preset superfast` 或 `fast`

## 📦 部署到雲平台

### Render.com（推薦，免費）

1. 在 Render.com 註冊
2. 連接 GitHub 倉庫
3. 選擇 Node 環境
4. 部署

### Railway.app（$5/月額度）

1. 連接 GitHub
2. 自動部署
3. 享受每月 $5 額度

## 📄 授權

MIT License

## 🤝 貢獻

歡迎提交 Issue 和 Pull Request！

---

**有問題？** 請提交 Issue 或聯繫開發者
