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
FONT_NAME = "Noto Sans CJK TC"

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
# ffmpeg / ffprobe 檢查
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
# 字幕文字清理（只給 SRT 用）
# =========================================================
def clean_subtitle_text_line(line: str) -> str:
    line = re.sub(r"<[^>]+>", "", line)
    line = re.sub(r"\{[^}]+\}", "", line)
    return line


def clean_srt_file(input_sub_path, output_sub_path):
    try:
        with open(input_sub_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        cleaned_lines = [clean_subtitle_text_line(line) for line in lines]

        with open(output_sub_path, "w", encoding="utf-8") as f:
            f.writelines(cleaned_lines)

        return True
    except Exception as e:
        print(f"SRT 清理失敗: {e}")
        return False


# =========================================================
# 字幕內容判斷：是否包含中日韓字元
# =========================================================
def contains_cjk(text: str) -> bool:
    return re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text) is not None


def detect_subtitle_mode(subtitle_path: str) -> str:
    """
    回傳:
    - 'cjk'   : 中文字幕 / 雙語字幕 / 含中日韓字
    - 'latin' : 純外文字幕
    """
    try:
        with open(subtitle_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        content = clean_subtitle_text_line(content)

        if contains_cjk(content):
            return "cjk"
        return "latin"

    except Exception as e:
        print(f"字幕類型判斷失敗，預設使用 cjk。錯誤: {e}")
        return "cjk"


# =========================================================
# 位置 / 品質參數
# =========================================================
def get_alignment_code(position: str) -> int:
    """
    ASS Alignment:
    2 = 底部置中
    5 = 中間置中
    8 = 上方置中
    """
    mapping = {
        "底部": 2,
        "中間": 5,
        "上方": 8
    }
    return mapping.get(position, 2)


def get_quality_params(quality: str):
    if quality == "快速":
        return {"preset": "veryfast", "crf": "25"}
    elif quality == "高品質":
        return {"preset": "medium", "crf": "19"}
    else:
        return {"preset": "fast", "crf": "22"}


# =========================================================
# 字幕解析度模式 -> 倍率策略
# =========================================================
def get_resolution_profile(mode: str, actual_video_height: int):
    """
    回傳:
    {
        "label": "1080p / 720p / 480p / 自動(...)",
        "size_multiplier": float,
        "margin_multiplier": float
    }

    規則：
    - 1080p：直接用你輸入的字級
    - 720p：自動放大
    - 480p：再放大
    - 自動偵測：依影片高度自動套用
    """
    if mode == "1080p":
        return {
            "label": "1080p",
            "size_multiplier": 1.0,
            "margin_multiplier": 1.0
        }

    if mode == "720p":
        return {
            "label": "720p",
            "size_multiplier": 1.5,
            "margin_multiplier": 1.2
        }

    if mode == "480p":
        return {
            "label": "480p",
            "size_multiplier": 2.2,
            "margin_multiplier": 1.5
        }

    # 自動偵測
    if actual_video_height >= 1000:
        return {
            "label": f"自動偵測 → 1080p 档位（影片高度 {actual_video_height}px）",
            "size_multiplier": 1.0,
            "margin_multiplier": 1.0
        }
    elif actual_video_height >= 650:
        return {
            "label": f"自動偵測 → 720p 档位（影片高度 {actual_video_height}px）",
            "size_multiplier": 1.5,
            "margin_multiplier": 1.2
        }
    else:
        return {
            "label": f"自動偵測 → 480p 档位（影片高度 {actual_video_height}px）",
            "size_multiplier": 2.2,
            "margin_multiplier": 1.5
        }


# =========================================================
# 建立 subtitles filter（SRT 用）
# =========================================================
def build_srt_subtitle_filter(
    subtitle_path,
    font_size,
    margin_v,
    alignment=2,
    outline=1.0,
    shadow=0
):
    safe_sub_path = subtitle_path.replace("\\", "/").replace(":", "\\:")

    style = (
        f"Fontname={FONT_NAME},"
        f"FontSize={font_size},"
        f"BorderStyle=1,"
        f"Outline={outline},"
        f"Shadow={shadow},"
        f"MarginV={margin_v},"
        f"Alignment={alignment}"
    )

    return f"subtitles='{safe_sub_path}':force_style='{style}'"


# =========================================================
# 建立 ASS filter（保留 ASS 原樣式）
# =========================================================
def build_ass_subtitle_filter(subtitle_path):
    safe_sub_path = subtitle_path.replace("\\", "/").replace(":", "\\:")
    return f"ass='{safe_sub_path}'"


# =========================================================
# 核心：影片與字幕合併
# =========================================================
def merge_video_subtitle(
    video_path,
    subtitle_path,
    cn_size,
    en_size,
    subtitle_position,
    outline_size,
    shadow_size,
    margin_bottom,
    quality_mode,
    subtitle_resolution_mode,
    preview_mode=False
):
    subtitle_path = normalize_gradio_file_path(subtitle_path)

    if not video_path or not subtitle_path:
        return None, "❌ 請確認已上傳影片與字幕檔案。"

    if not os.path.exists(video_path):
        return None, f"❌ 找不到影片檔案：{video_path}"

    if not os.path.exists(subtitle_path):
        return None, f"❌ 找不到字幕檔案：{subtitle_path}"

    cleanup_old_temp_files()
    task_id = str(uuid.uuid4())

    sub_ext = os.path.splitext(subtitle_path)[1].lower()
    prefix = "preview_" if preview_mode else "full_"
    output_path = os.path.join(TEMP_DIR, f"{prefix}{task_id}.mp4")

    # =====================================================
    # 影片資訊 / 品質設定
    # =====================================================
    actual_video_height = get_video_height(video_path)

    resolution_profile = get_resolution_profile(
        subtitle_resolution_mode,
        actual_video_height
    )
    size_multiplier = resolution_profile["size_multiplier"]
    margin_multiplier = resolution_profile["margin_multiplier"]
    resolution_label = resolution_profile["label"]

    quality_params = get_quality_params(quality_mode)
    preset = quality_params["preset"]
    crf = quality_params["crf"]

    subtitle_mode_text = ""
    font_info_text = ""
    final_sub_path = subtitle_path

    # =====================================================
    # ASS：保留原樣式
    # =====================================================
    if sub_ext == ".ass":
        video_filter = build_ass_subtitle_filter(subtitle_path)
        subtitle_mode_text = "ASS 字幕（保留原樣式）"
        font_info_text = "ASS 模式下沿用字幕檔內建樣式；解析度字級模式不套用。"

    # =====================================================
    # SRT：依解析度檔位倍率計算字級
    # =====================================================
    else:
        cleaned_sub_path = os.path.join(TEMP_DIR, f"clean_{task_id}.srt")
        if clean_srt_file(subtitle_path, cleaned_sub_path):
            final_sub_path = cleaned_sub_path
        else:
            final_sub_path = subtitle_path

        subtitle_mode = detect_subtitle_mode(final_sub_path)

        if subtitle_mode == "latin":
            base_font_size = en_size
            subtitle_mode_text = "純外文字幕"
        else:
            base_font_size = cn_size
            subtitle_mode_text = "中文字幕 / 雙語字幕"

        final_font_size = max(int(base_font_size * size_multiplier), 8)
        final_margin_v = max(int(margin_bottom * margin_multiplier), 0)
        alignment_code = get_alignment_code(subtitle_position)

        video_filter = build_srt_subtitle_filter(
            subtitle_path=final_sub_path,
            font_size=final_font_size,
            margin_v=final_margin_v,
            alignment=alignment_code,
            outline=outline_size,
            shadow=shadow_size
        )

        font_info_text = (
            f"字幕判定: {subtitle_mode_text}\n"
            f"字幕解析度模式: {resolution_label}\n"
            f"字級倍率: x{size_multiplier}\n"
            f"邊界距離倍率: x{margin_multiplier}\n"
            f"套用基準字體: {base_font_size}\n"
            f"實際輸出字體: {final_font_size}px\n"
            f"實際邊界距離: {final_margin_v}px"
        )

    mode_text = "【測試模式 - 僅擷取前2分鐘】" if preview_mode else "【正式完整模式】"

    info_msg = (
        f"{mode_text}\n"
        f"實際影片高度: {actual_video_height}px\n"
        f"字幕位置: {subtitle_position}\n"
        f"邊框粗細: {outline_size}\n"
        f"陰影大小: {shadow_size}\n"
        f"輸出品質: {quality_mode}\n"
        f"字幕模式: {subtitle_mode_text}\n"
    )

    if font_info_text:
        info_msg += font_info_text

    # =====================================================
    # FFmpeg 命令
    # =====================================================
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
        "-preset", preset,
        "-crf", crf,
        "-c:a", "copy",
        output_path
    ])

    print("\n========== FFmpeg 執行開始 ==========")
    print("影片路徑：", video_path)
    print("字幕原始路徑：", subtitle_path)
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

        # 成功後清理中間 srt
        if sub_ext != ".ass":
            cleaned_sub_path = os.path.join(TEMP_DIR, f"clean_{task_id}.srt")
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
def handle_full_merge(video, subtitle, cn_sz, en_sz, pos, outline, shadow, margin, quality, sub_res_mode):
    return merge_video_subtitle(
        video_path=video,
        subtitle_path=subtitle,
        cn_size=cn_sz,
        en_size=en_sz,
        subtitle_position=pos,
        outline_size=outline,
        shadow_size=shadow,
        margin_bottom=margin,
        quality_mode=quality,
        subtitle_resolution_mode=sub_res_mode,
        preview_mode=False
    )


def handle_preview_merge(video, subtitle, cn_sz, en_sz, pos, outline, shadow, margin, quality, sub_res_mode):
    return merge_video_subtitle(
        video_path=video,
        subtitle_path=subtitle,
        cn_size=cn_sz,
        en_size=en_sz,
        subtitle_position=pos,
        outline_size=outline,
        shadow_size=shadow,
        margin_bottom=margin,
        quality_mode=quality,
        subtitle_resolution_mode=sub_res_mode,
        preview_mode=True
    )


# =========================================================
# 啟動初始化
# =========================================================
check_ffmpeg_tools()
start_background_cleanup()


# =========================================================
# Gradio UI
# =========================================================
with gr.Blocks(theme=gr.themes.Soft(primary_hue=gr.themes.colors.indigo)) as demo:
    gr.Markdown("# 🎬 影片與字幕自動合併工具 v3.2")
    gr.Markdown(
        "支援：**SRT / ASS**、**預覽 2 分鐘**、**正式完整輸出**、"
        "**中文/外文分開字級**、**字幕位置 / 邊框 / 陰影 / 品質設定**、"
        "**字幕解析度模式（1080p / 720p / 480p / 自動）**。"
    )

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
                    maximum=80,
                    value=28,
                    step=1,
                    label="中文 / 雙語字幕基準大小",
                    info="1080p 基準值；720p / 480p 模式會自動放大"
                )

                en_size_input = gr.Slider(
                    minimum=6,
                    maximum=50,
                    value=14,
                    step=1,
                    label="純外文字幕基準大小",
                    info="1080p 基準值；720p / 480p 模式會自動放大"
                )

            with gr.Row():
                subtitle_resolution_mode_input = gr.Dropdown(
                    choices=["自動偵測", "1080p", "720p", "480p"],
                    value="1080p",
                    label="字幕解析度模式",
                    info="1080p=直接用輸入字級；720p/480p 會自動放大"
                )

                quality_input = gr.Dropdown(
                    choices=["快速", "標準", "高品質"],
                    value="標準",
                    label="輸出品質"
                )

            with gr.Row():
                subtitle_position_input = gr.Dropdown(
                    choices=["底部", "中間", "上方"],
                    value="底部",
                    label="字幕位置"
                )

                margin_input = gr.Slider(
                    minimum=0,
                    maximum=150,
                    value=15,
                    step=1,
                    label="底部距離 / 邊界距離"
                )

            with gr.Row():
                outline_input = gr.Slider(
                    minimum=0,
                    maximum=8,
                    value=1.5,
                    step=0.1,
                    label="字幕邊框粗細"
                )

                shadow_input = gr.Slider(
                    minimum=0,
                    maximum=8,
                    value=0,
                    step=0.1,
                    label="字幕陰影大小"
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
                placeholder="等待操作中...",
                lines=20
            )

    preview_inputs = [
        video_input,
        sub_input,
        cn_size_input,
        en_size_input,
        subtitle_position_input,
        outline_input,
        shadow_input,
        margin_input,
        quality_input,
        subtitle_resolution_mode_input
    ]

    btn_preview.click(
        fn=handle_preview_merge,
        inputs=preview_inputs,
        outputs=[video_output, status_output]
    )

    btn_submit.click(
        fn=handle_full_merge,
        inputs=preview_inputs,
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
