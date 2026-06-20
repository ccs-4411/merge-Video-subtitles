import os
import subprocess
import uuid
import re
import shutil
import time
import threading
import gradio as gr

# =========================================================
# 基本設定
# =========================================================
TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

TEMP_FILE_EXPIRE_HOURS = 6
PREVIEW_SECONDS = 120


# =========================================================
# 工具函式：背景清理舊暫存檔
# =========================================================
def cleanup_old_temp_files(folder=TEMP_DIR, expire_hours=TEMP_FILE_EXPIRE_HOURS):
    if not os.path.exists(folder):
        return
    now = time.time()
    expire_seconds = expire_hours * 3600
    try:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            try:
                mtime = os.path.getmtime(path)
                if now - mtime > expire_seconds:
                    os.remove(path)
                    print(f"[清理] 已刪除舊暫存檔: {path}")
            except Exception as e:
                print(f"[清理警告] 刪除檔案失敗 {path}: {e}")
    except Exception as e:
        print(f"[清理警告] 掃描暫存資料夾失敗: {e}")


def start_background_cleanup():
    def loop():
        while True:
            cleanup_old_temp_files()
            time.sleep(3600)
    cleanup_old_temp_files()
    t = threading.Thread(target=loop, daemon=True)
    t.start()


# =========================================================
# 工具函式：確認 ffmpeg / ffprobe 是否存在
# =========================================================
def check_ffmpeg_tools():
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if not ffmpeg_path:
        print("【系統警告】找不到 ffmpeg，請確認 Railway 已安裝 ffmpeg。")
    else:
        print(f"【系統初始化】ffmpeg 路徑: {ffmpeg_path}")
    if not ffprobe_path:
        print("【系統警告】找不到 ffprobe，請確認 Railway 已安裝 ffmpeg。")
    else:
        print(f"【系統初始化】ffprobe 路徑: {ffprobe_path}")


# =========================================================
# 影片解析度精準偵測 (寬與高)
# =========================================================
def get_video_dimensions(video_path):
    cmd_h = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=height", "-of", "csv=s=x:p=0", video_path
    ]
    cmd_w = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width", "-of", "csv=s=x:p=0", video_path
    ]
    try:
        res_h = subprocess.run(cmd_h, stdout=subprocess.PIPE, text=True, check=True)
        res_w = subprocess.run(cmd_w, stdout=subprocess.PIPE, text=True, check=True)
        height = int(res_h.stdout.strip())
        width = int(res_w.stdout.strip())
        return width, height
    except Exception as e:
        print(f"偵測影片解析度失敗，使用保底 1920x1080。錯誤: {e}")
        return 1920, 1080


# =========================================================
# 核心動態轉換：將 .srt 轉為「解析度100%對齊」的雙語 .ass 格式
# =========================================================
def convert_srt_to_dual_ass(srt_path, ass_path, font_size, margin_v, res_x, res_y):
    try:
        with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # 💡 外文字幕大小設定為中文字幕的 75%
        sub_size = max(int(font_size * 0.75), 10)
        # 💡 字幕邊框粗細根據畫布高度動態調整，防止小解析度時黑框太粗
        outline_w = max(round(res_y / 400, 1), 1.0)

        # 💡 關鍵修正：PlayResX 與 PlayResY 完全對齊輸出的影片解析度！
        ass_header = (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            "Collisions: Normal\n"
            f"PlayResX: {res_x}\n"
            f"PlayResY: {res_y}\n"
            "Timer: 100.0000\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
             f"Style: Default,WenQuanYi Zen Hei,{font_size},&H00FFFFFF&,&H000000FF&,&H00000000&,&H00000000&,1,0,0,0,100,100,0,0,1,{outline_w},0,2,10,10,{margin_v},1\n"
             f"Style: Trans,WenQuanYi Zen Hei,{sub_size},&H0000FFFF&,&H000000FF&,&H00000000&,&H00000000&,0,0,0,0,100,100,0,0,1,{outline_w},0,2,10,10,{margin_v},1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

        def convert_time(srt_time):
            srt_time = srt_time.strip().replace(",", ".")
            match = re.match(r"(\d+):(\d+):(\d+)\.(\d+)", srt_time)
            if match:
                h = int(match.group(1))
                m = match.group(2)
                s = match.group(3)
                ms = match.group(4)[:2]
                return f"{h}:{m}:{s}.{ms}"
            return "0:00:00.00"

        blocks = content.replace("\r\n", "\n").split("\n\n")
        dialogues = []

        for block in blocks:
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if len(lines) < 3:
                continue

            time_idx = -1
            for idx, line in enumerate(lines):
                if "-->" in line:
                    time_idx = idx
                    break
            
            if time_idx == -1 or time_idx + 1 >= len(lines):
                continue

            times = lines[time_idx].split("-->")
            start_t = convert_time(times[0])
            end_t = convert_time(times[1])

            sub_text_lines = lines[time_idx + 1:]
            
            cleaned_texts = []
            for tl in sub_text_lines:
                t_clean = re.sub(r"<[^>]+>", "", tl)
                t_clean = re.sub(r"\{[^}]+\}", "", t_clean).strip()
                if t_clean:
                    cleaned_texts.append(t_clean)

            if not cleaned_texts:
                continue

            # 寫入 ASS 事件軌 (\N 控制分行與間距)
            if len(cleaned_texts) >= 2:
                dialogues.append(f"Dialogue: 0,{start_t},{end_t},Default,,0,0,0,,{cleaned_texts[0]}")
                dialogues.append(f"Dialogue: 1,{start_t},{end_t},Trans,,0,0,0,,\\N{cleaned_texts[1]}")
            else:
                dialogues.append(f"Dialogue: 0,{start_t},{end_t},Default,,0,0,0,,{cleaned_texts[0]}")

        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_header + "\n".join(dialogues))
        return True
    except Exception as e:
        print(f"SRT 轉動態 ASS 失敗: {e}")
        return False


# =========================================================
# 核心：影片與字幕合併
# =========================================================
def merge_video_subtitle(video_path, subtitle_path, cn_size, target_resolution, force_ass_style, preview_mode=False):
    if not video_path or not subtitle_path:
        return None, "❌ 請確認已上傳影片與字幕檔案。"

    cleanup_old_temp_files()
    task_id = str(uuid.uuid4())
    sub_ext = os.path.splitext(subtitle_path)[1].lower()
    
    # 获取原始视频寬高
    orig_w, orig_h = get_video_dimensions(video_path)
    
    # 決定目標輸出解析度
    if target_resolution == "1080p (1920x1080)":
        target_w, target_h = 1920, 1080
    elif target_resolution == "720p (1280x720)":
        target_w, target_h = 1280, 720
    elif target_resolution == "480p (854x480)":
        target_w, target_h = 854, 480
    else:
        target_w, target_h = orig_w, orig_h

    # 💡 核心亮點：直接將滑桿大小作為該畫布下的絕對像素大小，並依高度動態計算下邊距
    final_cn_size = int(cn_size)
    final_margin_v = max(int(target_h * 0.04), 8) # 下邊距維持在畫布高度的 4%

    final_sub_path = os.path.join(TEMP_DIR, f"render_{task_id}.ass")
    use_force_style = True

    if sub_ext == ".srt":
        # 💡 將目標寬高傳入，建立 1:1 完美對齊的字幕畫布
        if not convert_srt_to_dual_ass(subtitle_path, final_sub_path, final_cn_size, final_margin_v, target_w, target_h):
            final_sub_path = subtitle_path
            use_force_style = False
    elif sub_ext in [".ass", ".ssa"]:
        if force_ass_style:
            final_sub_path = subtitle_path
            use_force_style = True
        else:
            final_sub_path = subtitle_path
            use_force_style = False
    else:
        final_sub_path = subtitle_path
        use_force_style = False

    prefix = "preview_" if preview_mode else "full_"
    output_path = os.path.join(TEMP_DIR, f"{prefix}{task_id}.mp4")

    # ========== 建立 FFmpeg 濾鏡鏈 ==========
    filter_elements = []
    if target_h != orig_h:
        filter_elements.append(f"scale=-2:{target_h}")

    safe_sub_path = final_sub_path.replace("\\", "/").replace(":", "\\:")
    
    if use_force_style and sub_ext != ".srt":
        style = (
            f"Fontname=WenQuanYi Zen Hei,"
            f"FontSize={final_cn_size},"
            f"BorderStyle=1,"
            f"Outline=1.2,"
            f"Shadow=0,"
            f"MarginV={final_margin_v}"
        )
        sub_filter = f"subtitles='{safe_sub_path}':force_style='{style}'"
    else:
        sub_filter = f"subtitles='{safe_sub_path}'"
    
    filter_elements.append(sub_filter)
    video_filter = ",".join(filter_elements)

    mode_text = "【測試模式 - 僅擷取前2分鐘】" if preview_mode else "【正式完整模式】"
    info_msg = (
        f"{mode_text}\n"
        f"影片維度: 原始 {orig_w}x{orig_h} -> 輸出 {target_w}x{target_h}\n"
        f"畫布對齊: 字幕與影片輸出解析度已 1:1 完美綁定\n"
        f"預期樣式: 中文大小 {final_cn_size}px (白色) ｜ 英文大小 {int(final_cn_size*0.75)}px (黃色)"
    )

    cmd = ["ffmpeg", "-y", "-i", video_path]
    if preview_mode:
        cmd.extend(["-t", str(PREVIEW_SECONDS)])
    cmd.extend([
        "-vf", video_filter,
        "-c:v", "libx264",
        "-preset", "superfast",
        "-crf", "23",
        "-c:a", "copy",
        output_path
    ])

    print("\n========== FFmpeg 執行開始 ==========")
    print("FFmpeg 命令：")
    print(" ".join(cmd))
    print("====================================\n")

    try:
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", timeout=300)
        if process.returncode != 0:
            print("FFmpeg 錯誤日誌：\n", process.stderr)
            return None, f"❌ FFmpeg 壓製失敗。\n\n{process.stderr}"

        if os.path.exists(final_sub_path) and "render_" in final_sub_path:
            try: os.remove(final_sub_path)
            except: pass

        if not os.path.exists(output_path):
            return None, "❌ FFmpeg 看似成功，但找不到輸出檔案。"

        return (
            output_path,
            f"✨ 影片與字幕合併成功！\n\n【系統通知】\n{info_msg}\n檔案已就緒，可於右側直接播放或下載。"
        )
    except subprocess.TimeoutExpired:
        return None, "❌ 壓製逾時！請嘗試降低輸出解析度。"
    except Exception as e:
        return None, f"❌ 伺服器內部發生錯誤：{str(e)}"


# =========================================================
# Gradio 按鈕分流函式
# =========================================================
def handle_full_merge(video, subtitle, cn_sz, res, force_ass):
    return merge_video_subtitle(video, subtitle, cn_sz, res, force_ass, preview_mode=False)

def handle_preview_merge(video, subtitle, cn_sz, res, force_ass):
    return merge_video_subtitle(video, subtitle, cn_sz, res, force_ass, preview_mode=True)


# =========================================================
# 啟動初始化
# =========================================================
check_ffmpeg_tools()
start_background_cleanup()


# =========================================================
# Gradio UI
# =========================================================
with gr.Blocks(theme=gr.themes.Soft(primary_hue=gr.themes.colors.indigo)) as demo:
    gr.Markdown("# 🎬 影片與字幕自動合併工具 (解析度自適應終極版)")

    with gr.Row():
        with gr.Column():
            video_input = gr.Video(
                label="1. 上傳原始影片 (MP4 / MKV)",
                height=360
            )

            sub_input = gr.File(
                label="2. 上傳字幕檔案 (.srt / .ass / .ssa)",
                file_types=[".srt", ".ass", ".ssa"]
            )

            with gr.Row():
                resolution_input = gr.Dropdown(
                    choices=["原始解析度", "1080p (1920x1080)", "720p (1280x720)", "480p (854x480)"],
                    value="原始解析度",
                    label="3. 輸出影片解析度",
                    info="對於 360p 小影片，維持「原始解析度」或選「480p/720p」速度最快！"
                )
                
                force_ass_style_input = gr.Checkbox(
                    label="強制修改原生 ASS 字幕大小",
                    value=True,
                    info="若為 .srt 字幕，系統將會100%自動重製雙語規格，此勾選無影響。"
                )

            with gr.Row():
                cn_size_input = gr.Slider(
                    minimum=12,
                    maximum=60,         
                    value=24,            
                    step=1,
                    label="主要中文字幕大小 (像素單位)",
                    info="💡 提示：480p/360p 建議設定 20~26；720p 建議 28~36；1080p 建議 40~52。"
                )

            with gr.Row():
                btn_preview = gr.Button("⏱️ 測試合併（僅前2分鐘）", variant="secondary")
                btn_submit = gr.Button("🚀 開始正式完整合併", variant="primary")

        with gr.Column():
            video_output = gr.Video(
                label="4. 合併結果影片",
                height=360
            )

            status_output = gr.Textbox(
                label="執行狀態 / 錯誤日誌",
                interactive=False,
                placeholder="等待操作中..."
            )

    btn_preview.click(
        fn=handle_preview_merge,
        inputs=[video_input, sub_input, cn_size_input, resolution_input, force_ass_style_input],
        outputs=[video_output, status_output]
    )

    btn_submit.click(
        fn=handle_full_merge,
        inputs=[video_input, sub_input, cn_size_input, resolution_input, force_ass_style_input],
        outputs=[video_output, status_output]
    )


# =========================================================
# Railway 啟動
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        show_error=True
    )
