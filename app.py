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

# 自動清理：超過幾小時的暫存檔刪除
TEMP_FILE_EXPIRE_HOURS = 6

# 預覽模式只輸出前 120 秒
PREVIEW_SECONDS = 120


# =========================================================
# 工具函式：背景清理舊暫存檔
# =========================================================
def cleanup_old_temp_files(folder=TEMP_DIR, expire_hours=TEMP_FILE_EXPIRE_HOURS):
    """
    刪除超過 expire_hours 的暫存檔
    """
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
    """
    啟動時先清一次，之後每 1 小時清一次
    """
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
# 影片解析度偵測
# =========================================================
def get_video_height(video_path):
    """
    使用 ffprobe 自動偵測影片的實際垂直解析度(高度)
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=height",
        "-of", "csv=s=x:p=0",
        video_path
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        height = int(result.stdout.strip())
        return height
    except Exception as e:
        print(f"偵測影片解析度失敗，保底設定為 1080。錯誤: {e}")
        return 1080


# =========================================================
# 字幕清理
# =========================================================
def clean_and_prepare_srt(input_sub_path, output_sub_path):
    """
    強力移除字幕內所有干擾樣式，還原為最純淨的純文字 SRT
    注意：
    - 這樣做對 ASS 也能讀，但 ASS 的格式化會被清掉
    - 如果你希望保留 ASS 樣式，就不要清 ASS
    """
    try:
        with open(input_sub_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        cleaned_lines = []
        for line in lines:
            # 去除 HTML 標籤
            line = re.sub(r"<[^>]+>", "", line)
            # 去除 {xxx} 樣式標記
            line = re.sub(r"\{[^}]+\}", "", line)
            cleaned_lines.append(line)

        with open(output_sub_path, "w", encoding="utf-8") as f:
            f.writelines(cleaned_lines)

        return True
    except Exception as e:
        print(f"字幕純淨化失敗: {e}")
        return False


# =========================================================
# 產生字幕濾鏡
# =========================================================
def build_subtitle_filter(subtitle_path, font_size, margin_v):
    """
    產生 ffmpeg subtitles filter 字串
    """
    # Windows / Linux 路徑保護
    safe_sub_path = subtitle_path.replace("\\", "/").replace(":", "\\:")

    # 字型名稱可依你需要改
    # Railway 若有裝 fonts-noto-cjk，通常 Noto Sans CJK TC 可用
    style = (
        f"Fontname=Noto Sans CJK TC,"
        f"FontSize={font_size},"
        f"BorderStyle=1,"
        f"Outline=1.0,"
        f"Shadow=0,"
        f"MarginV={margin_v}"
    )

    return f"subtitles='{safe_sub_path}':force_style='{style}'"


# =========================================================
# 核心：影片與字幕合併
# =========================================================
def merge_video_subtitle(video_path, subtitle_path, cn_size, en_size, preview_mode=False):
    """
    video_path: 影片路徑
    subtitle_path: 字幕路徑
    cn_size: 中文/雙語字幕基準大小
    en_size: 純外文字幕基準大小 (目前保留參數，未做自動語系判斷)
    preview_mode: True 時只輸出前 2 分鐘
    """
    if not video_path or not subtitle_path:
        return None, "❌ 請確認已上傳影片與字幕檔案。"

    # 啟動前先清一次舊檔
    cleanup_old_temp_files()

    task_id = str(uuid.uuid4())

    # 產出清理後字幕檔
    cleaned_sub_path = os.path.join(TEMP_DIR, f"clean_{task_id}.srt")

    # 判斷副檔名
    sub_ext = os.path.splitext(subtitle_path)[1].lower()

    # 預設：最後給 ffmpeg 用的字幕檔路徑
    final_sub_path = subtitle_path

    # 對 .srt 做清理；如果是 .ass，這版也會清成 .srt 風格純文字
    # 若你想保留 ASS 樣式，可改成只處理 .srt
    if sub_ext in [".srt", ".ass"]:
        if clean_and_prepare_srt(subtitle_path, cleaned_sub_path):
            final_sub_path = cleaned_sub_path
        else:
            final_sub_path = subtitle_path

    # 區分測試版影片與正式版影片命名
    prefix = "preview_" if preview_mode else "full_"
    output_path = os.path.join(TEMP_DIR, f"{prefix}{task_id}.mp4")

    # ================= 核心智能縮放邏輯 =================
    video_height = get_video_height(video_path)
    scale_factor = video_height / 1080.0

    # 目前先沿用你的做法：主要使用 cn_size
    # en_size 參數保留，之後若你要做「純英文字幕判斷」再接進來
    final_cn_size = max(int(cn_size * scale_factor), 8)
    final_margin_v = max(int(15 * scale_factor), 6)

    video_filter = build_subtitle_filter(
        subtitle_path=final_sub_path,
        font_size=final_cn_size,
        margin_v=final_margin_v
    )

    mode_text = "【測試模式 - 僅擷取前2分鐘】" if preview_mode else "【正式完整模式】"
    info_msg = (
        f"{mode_text}\n"
        f"影片高度: {video_height}px。\n"
        f"套用絕對像素大小 -> 中文預估: {final_cn_size}px。"
    )

    # ================= FFmpeg 指令 =================
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path
    ]

    # 預覽模式只輸出前 PREVIEW_SECONDS 秒
    if preview_mode:
        cmd.extend(["-t", str(PREVIEW_SECONDS)])

    cmd.extend([
        "-vf", video_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "copy",
        output_path
    ])

    print("\n========== FFmpeg 執行開始 ==========")
    print("FFmpeg 命令：")
    print(" ".join(cmd))
    print("====================================\n")

    try:
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8"
        )

        if process.returncode != 0:
            print("FFmpeg 錯誤日誌：\n", process.stderr)
            return None, f"❌ FFmpeg 壓製失敗。\n\n{process.stderr}"

        # 成功後，若有建立清理字幕檔就保留或刪除皆可
        # 這裡直接刪掉中間清理字幕檔
        if os.path.exists(cleaned_sub_path):
            try:
                os.remove(cleaned_sub_path)
            except Exception:
                pass

        if not os.path.exists(output_path):
            return None, "❌ FFmpeg 看似成功，但找不到輸出檔案。"

        return (
            output_path,
            f"✨ 影片與字幕合併成功！\n\n【系統通知】\n{info_msg}\n檔案已就緒，可於右側直接播放或下載。"
        )

    except Exception as e:
        return None, f"❌ 伺服器內部發生錯誤：{str(e)}"


# =========================================================
# Gradio 按鈕分流函式
# =========================================================
def handle_full_merge(video, subtitle, cn_sz, en_sz):
    return merge_video_subtitle(video, subtitle, cn_sz, en_sz, preview_mode=False)


def handle_preview_merge(video, subtitle, cn_sz, en_sz):
    return merge_video_subtitle(video, subtitle, cn_sz, en_sz, preview_mode=True)


# =========================================================
# 啟動初始化
# =========================================================
check_ffmpeg_tools()
start_background_cleanup()


# =========================================================
# Gradio UI
# =========================================================
with gr.Blocks(theme=gr.themes.Soft(primary_hue=gr.themes.colors.indigo)) as demo:
    gr.Markdown("# 🎬 影片與字幕自動合併工具")

    with gr.Row():
        with gr.Column():
            video_input = gr.Video(
                label="1. 上傳原始影片 (MP4 / MKV)",
                height=360
            )

            sub_input = gr.File(
                label="2. 上傳字幕檔案 (.srt / .ass)",
                file_types=[".srt", ".ass"]
            )

            with gr.Row():
                cn_size_input = gr.Slider(
                    minimum=10,
                    maximum=60,
                    value=20,
                    step=1,
                    label="中文/雙語字幕基準大小",
                    info="以 1080p 為基礎的中文尺寸（預設 20）"
                )

                en_size_input = gr.Slider(
                    minimum=6,
                    maximum=40,
                    value=12,
                    step=1,
                    label="純外文字幕基準大小",
                    info="目前先保留參數，後續可擴充自動判斷純英文字幕尺寸"
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
        inputs=[video_input, sub_input, cn_size_input, en_size_input],
        outputs=[video_output, status_output]
    )

    btn_submit.click(
        fn=handle_full_merge,
        inputs=[video_input, sub_input, cn_size_input, en_size_input],
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
