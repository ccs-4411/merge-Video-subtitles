import os
import subprocess
import uuid
import re
import gradio as gr

def init_system_fonts():
    """在 Linux 伺服器背景自動安裝中文字型包"""
    if os.name != 'nt':
        try:
            print("【系統初始化】正在檢查並自動安裝 Linux 系統中文字型包...")
            subprocess.run(["apt-get", "update", "-qq"], check=False)
            subprocess.run(["apt-get", "install", "-y", "-qq", "fonts-noto-cjk"], check=False)
            print("【系統初始化】Linux 系統中文字型包安裝成功！")
        except Exception as e:
            print(f"【系統警告】自動安裝字型時發生錯誤: {e}")

# 啟動時補齊系統字型
init_system_fonts()


def get_video_height(video_path):
    """使用 ffprobe 自動偵測影片的實際垂直解析度(高度)"""
    cmd = [
        'ffprobe', '-v', 'error', 
        '-select_streams', 'v:0', 
        '-show_entries', 'stream=height', 
        '-of', 'csv=s=x:p=0', 
        video_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        height = int(result.stdout.strip())
        return height
    except Exception as e:
        print(f"偵測影片解析度失敗，保底設定為 1080. 錯誤: {e}")
        return 1080


def clean_and_prepare_srt(input_sub_path, output_sub_path):
    """強力移除字幕內所有干擾樣式，還原為最純淨的純文字 SRT"""
    try:
        with open(input_sub_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        cleaned_lines = []
        for line in lines:
            line = re.sub(re.compile(r'<[^>]+>'), '', line)
            line = re.sub(re.compile(r'\{[^}]+\}'), '', line)
            cleaned_lines.append(line)
            
        with open(output_sub_path, 'w', encoding='utf-8') as f:
            f.writelines(cleaned_lines)
        return True
    except Exception as e:
        print(f"字幕純淨化失敗: {e}")
        return False


def merge_video_subtitle(video_path, subtitle_path, cn_size, en_size, preview_mode=False):
    if not video_path or not subtitle_path:
        return None, "❌ 請確認已上傳影片與字幕檔案。"

    UPLOAD_FOLDER = "temp_files"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    task_id = str(uuid.uuid4())
    
    cleaned_sub_path = os.path.join(UPLOAD_FOLDER, f"clean_{task_id}.srt")
    if not clean_and_prepare_srt(subtitle_path, cleaned_sub_path):
        cleaned_sub_path = subtitle_path

    # 區分測試版影片與正式版影片命名
    prefix = "preview_" if preview_mode else "full_"
    output_path = os.path.join(UPLOAD_FOLDER, f"{prefix}{task_id}.mp4")
    safe_sub_path = cleaned_sub_path.replace('\\', '/').replace(':', '\\:')

    # ================= 核心智能縮放邏輯 =================
    video_height = get_video_height(video_path)
    scale_factor = video_height / 1080.0
    
    final_cn_size = max(int(cn_size * scale_factor), 8)
    
    video_filter = (
        f"subtitles='{safe_sub_path}':"
        f"force_style='Fontname=Noto Sans CJK TC,FontSize={final_cn_size},"
        f"BorderStyle=1,Outline=1.0,Shadow=0,MarginV={int(15 * scale_factor)}'"
    )
    
    mode_text = "【測試模式 - 僅擷取前2分鐘】" if preview_mode else "【正式完整模式】"
    info_msg = f"{mode_text}\n影片高度: {video_height}px。\n套用絕對像素大小 -> 中文預估: {final_cn_size}px。"

    # 基本 FFmpeg 指令
    cmd = [
        'ffmpeg', '-y',
        '-i', video_path
    ]
    
    # 如果是試看模式，限制輸出時間為 120 秒 (2分鐘)
    if preview_mode:
        cmd.extend(['-t', '120'])
        
    cmd.extend([
        '-vf', video_filter,
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '22',
        '-c:a', 'copy',
        output_path
    ])

    try:
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        
        if process.returncode != 0:
            print("FFmpeg 錯誤日誌:\n", process.stderr)
            return None, f"❌ FFmpeg 壓製失敗。\n{process.stderr}"
            
        if os.path.exists(cleaned_sub_path) and cleaned_sub_path != subtitle_path:
            os.remove(cleaned_sub_path)
            
        return output_path, f"✨ 影片與字幕合併成功！\n【系統通知】\n{info_msg}\n檔案已就緒，可於右側直接播放或下載。"
        
    except Exception as e:
        return None, f"❌ 伺服器內部發生錯誤：{str(e)}"

# 提供給 Gradio 按鈕的分流包裝函式
def handle_full_merge(video, subtitle, cn_sz, en_sz):
    return merge_video_subtitle(video, subtitle, cn_sz, en_sz, preview_mode=False)

def handle_preview_merge(video, subtitle, cn_sz, en_sz):
    return merge_video_subtitle(video, subtitle, cn_sz, en_sz, preview_mode=True)


# 建立 Gradio 介面 (已清除舊的說明字串)
with gr.Blocks() as demo:
    gr.Markdown("# 🎬 影片與字幕自動合併工具")
    
    with gr.Row():
        with gr.Column():
            video_input = gr.Video(label="1. 上傳原始影片 (MP4 / MKV)", height=360)
            sub_input = gr.File(label="2. 上傳字幕檔案 (.srt / .ass)", file_types=[".srt", ".ass"])
            
            with gr.Row():
                cn_size_input = gr.Slider(
                    minimum=10, maximum=60, value=20, step=1, 
                    label="中文/雙語字幕基準大小", info="以 1080p 為基礎的中文尺寸 (內定 20)"
                )
                en_size_input = gr.Slider(
                    minimum=6, maximum=40, value=12, step=1, 
                    label="純外文字幕基準大小", info="以 1080p 為基礎的外文尺寸 (內定 12)"
                )
            
            with gr.Row():
                # 新增的試看按鈕
                btn_preview = gr.Button("⏱️ 測試合併 (僅前2分鐘)", variant="secondary")
                # 正式合併按鈕
                btn_submit = gr.Button("🚀 開始正式完整合併", variant="primary")
            
        with gr.Column():
            video_output = gr.Video(label="4. 合併結果影片 (固定高度不放大)", height=360)
            status_output = gr.Textbox(label="執行狀態/錯誤日誌", interactive=False, placeholder="等待操作中...")

    # 綁定試看按鈕事件 (傳入 preview_mode=True)
    btn_preview.click(
        fn=handle_preview_merge,
        inputs=[video_input, sub_input, cn_size_input, en_size_input],
        outputs=[video_output, status_output]
    )

    # 綁定正式按鈕事件 (傳入 preview_mode=False)
    btn_submit.click(
        fn=handle_full_merge,
        inputs=[video_input, sub_input, cn_size_input, en_size_input],
        outputs=[video_output, status_output]
    )

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft(primary_hue=gr.themes.colors.indigo))