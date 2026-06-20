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
# 背景清理舊暫存檔
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
# 檢查 ffmpeg / ffprobe
# =========================================================
def check_ffmpeg_tools():
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    print("========== 系統檢查 ==========")
    print("PATH =", os.environ.get("PATH", ""))

    if ffmpeg_path:
        print(f"ffmpeg 路徑: {ffmpeg_path}")
        try:
            r = subprocess.run(
                ["ffmpeg", "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            print("ffmpeg -version 回傳碼:", r.returncode)
            print((r.stdout or r.stderr)[:500])
        except Exception as e:
            print("ffmpeg 執行失敗:", e)
    else:
        print("找不到 ffmpeg")

    if ffprobe_path:
        print(f"ffprobe 路徑: {ffprobe_path}")
    else:
        print("找不到 ffprobe")

    print("=============================")


# =========================================================
# 影片解析度偵測
# =========================================================
def get_video_height(video_path):
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
    移除字幕中的 HTML 標籤與 {xxx} 樣式
    注意：
    - 若上傳的是 ASS，這樣做會把 ASS 樣式清掉
    - 如果你之後想保留 ASS 樣式，可以改成只清理 .srt
    """
    try:
        with open(input_sub_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        cleaned_lines = []
        for line in lines:
            line = re.sub(r"<[^>]+>", "", line)
            line = re.sub(r"\{[^}]+\}", "", line)
            cleaned_lines.append(line)

        with open(output_sub_path, "w", encoding="utf-8") as f:
            f.writelines(cleaned_lines)

        return True
    except Exception as e:
        print(f"字幕純淨化失敗: {e}")
        return False


# =========================================================
# 建立 subtitles filter
# =========================================================
def build_subtitle_filter(subtitle_path, font_size, margin_v):
    safe_sub_path = subtitle_path.replace("\\", "/").replace(":", "\\:")

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
# Gradio File 路徑標準化
# =========================================================
def normalize_gradio_file_path(file_obj):
    if file_obj is None:
        return None

    if isinstance(file_obj, str):
        return file_obj

    if isinstance(file_obj, dict):
        for key in ["name", "path"]:
            if key in file_obj and file_obj[key]:
                return file_obj[key]

    if hasattr(file_obj, "name"):
        return file_obj.name

    return str(file_obj)


# =========================================================
# 核心：影片與字幕合併
# =========================================================
def merge_video_subtitle(video_path, subtitle_path, cn_size, en_size, preview_mode=False):
    subtitle_path = normalize_gradio_file_path(subtitle_path)

    if not video_path or not subtitle_path:
        return None, "❌ 請確認已上傳影片與字幕檔案。"

    if not os.path.exists(video_path):
        return None, f"❌ 找不到影片檔案：{video_path}"

    if not os.path.exists(subtitle_path):
        return None, f"❌ 找不到字幕檔案：{subtitle_path}"

    cleanup_old_temp_files()
    task_id = str(uuid.uuid4())

    cleaned_sub_path = os.path.join(TEMP_DIR, f"clean_{task_id}.srt")
    sub_ext = os.path.splitext(subtitle_path)[1].lower()

    final_sub_path = subtitle_path

    if sub_ext in [".srt", ".ass"]:
        if clean_and_prepare_srt(subtitle_path, cleaned_sub_path):
            final_sub_path = cleaned_sub_path
        else:
            final_sub_path = subtitle_path

    prefix = "preview_" if preview_mode else "full_"
    output_path = os.path.join(TEMP_DIR, f"{prefix}{task_id}.mp4")

    # 智能縮放
    video_height = get_video_height(video_path)
    scale_factor = video_height / 1080.0

    # 目前仍沿用你的邏輯：主要使用 cn_size
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

    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path
    ]

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
    print("影片路徑：", video_path)
    print("字幕路徑：", subtitle_path)
    print("實際使用字幕：", final_sub_path)
    print("輸出路徑：", output_path)
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
# 按鈕包裝
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
# 啟動
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        show_error=True
    )
