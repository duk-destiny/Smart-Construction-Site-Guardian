# -*- coding: utf-8 -*-
"""Demo video generator v3 — screenshot slideshow + per-sentence TTS + PIL subtitles.

Fixes all 5 issues from previous version:
  1. No repeating subtitles (one subtitle per sentence, PIL-rendered)
  2. Perfect alignment (screenshot duration == audio duration == subtitle duration)
  3. Starts directly from login (no system startup wait)
  4. Covers ALL modules (upload/Mock, agents, report, history, realtime, admin, diag)
  5. Mentions specific module functions in narration
  6. Saves all screenshots for documentation
"""
from __future__ import annotations
import os
os.environ["PYTHONUTF8"] = "1"
import asyncio, subprocess, sys, time, shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
DEMO  = BASE / "data" / "demo_video"
SHOTS = DEMO / "screenshots"
TMP   = DEMO / "_tmp"
FFPROBE = r"D:\ffmpeg-2026-03-26-git-fd9f1e9c52-full_build\ffmpeg-2026-03-26-git-fd9f1e9c52-full_build\bin\ffprobe.exe"
FFMPEG  = r"D:\ffmpeg-2026-03-26-git-fd9f1e9c52-full_build\ffmpeg-2026-03-26-git-fd9f1e9c52-full_build\bin\ffmpeg.exe"
TEST_IMG = str(BASE / "data" / "raw" / "construction-ppe" / "images" / "test" / "image1.jpeg")
APP  = "http://127.0.0.1:8501"
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
W, H = 1280, 720

# ── Narration segments: (screenshot_id, text) ──
# Each sentence = one subtitle entry, perfectly synced to its TTS audio
SEGMENTS = [
    # 1. Login
    ("01_login", "欢迎来到智护工地安全智能系统。"),
    ("01_login", "在登录页输入管理员账号和密码，点击登录进入系统。"),
    # 2. Upload & Work Permit (Mock mode visible)
    ("02_upload", "进入上传与作业票据页面。"),
    ("02_upload", "系统支持动火作业和施工PPE两种检测场景。"),
    ("02_upload", "上传现场图片后，填写作业票信息，包括动火级别、监火人、作业区域、灭火器配置等。"),
    ("02_upload", "当前无模型加载时，系统自动降级为Mock模式演示完整流程。"),
    ("02_upload", "填写完成后点击开始智能研判。"),
    # 3. Multi-Agent Analysis
    ("03_agents", "进入多Agent研判页面，点击运行，五个Agent协同工作。"),
    ("03b_agents_result", "感知视觉Agent识别安全装备与火情隐患。"),
    ("03b_agents_result", "安全规范Agent基于RAG知识库进行合规判定。"),
    ("03b_agents_result", "风险融合Agent综合评估风险等级。"),
    ("03b_agents_result", "复核Agent判断是否需要人工复核。"),
    ("03b_agents_result", "闭环处置Agent生成整改工单和工人白话提示。"),
    # 4. Report / Work Order
    ("04_report", "进入工单页面，展示整改工单详情，包括隐患描述、违反规范条款和整改要求。"),
    ("04_report", "支持人工改判风险等级、逐目标纠偏生成训练样本、导出Excel台账。"),
    # 5. History & Analysis
    ("05_history", "进入检测历史与分析页面，按日期范围筛选检测记录。"),
    ("05_history", "查看合规率趋势和类别命中分布统计图表，支持导出CSV明细。"),
    # 6. Realtime Camera Monitor
    ("06_realtime", "进入实时摄像头监测页面。"),
    ("06_realtime", "支持浏览器摄像头实时捕获和多路RTSP视频源接入。"),
    ("06_realtime", "红黄绿三色框标注合规状态，不合规时自动播放警报并弹窗提醒。"),
    ("06_realtime", "连续监控模式可自动刷新下一帧，实现全天候无人值守。"),
    # 7. Admin
    ("07_admin", "进入管理端，支持模型版本注册与切换、知识库规范维护。"),
    ("07_admin", "以及通知渠道配置、训练反馈样本审核等管理功能。"),
    # 8. Diagnostics
    ("08_diag", "进入系统自检页面，检查模型加载状态、数据库连接、各服务运行情况。"),
    # 9. Ending
    ("09_end", "以上是智护工地安全智能系统的完整功能演示，感谢观看。"),
]


# ═══════════════════════════════════════════════════════
#  Phase 1: TTS
# ═══════════════════════════════════════════════════════
async def _gen_tts(text: str, outpath: Path, retries: int = 3):
    import edge_tts
    for attempt in range(retries):
        try:
            await asyncio.wait_for(
                edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(str(outpath)),
                timeout=30.0)
            if outpath.exists() and outpath.stat().st_size > 100:
                return
            print(f"    retry {attempt+1}: empty file", flush=True)
        except Exception as e:
            print(f"    retry {attempt+1}: {e}", flush=True)
        outpath.unlink(missing_ok=True)
        await asyncio.sleep(2)
    raise RuntimeError(f"TTS failed after {retries} retries: {text[:30]}")


def _audio_dur(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    return float(r.stdout.strip())


def gen_tts_all():
    print(">>> [1/6] TTS generation...", flush=True)
    for i, (shot, text) in enumerate(SEGMENTS):
        mp3 = TMP / f"audio_{i:03d}.mp3"
        if mp3.exists() and mp3.stat().st_size > 100:
            dur = _audio_dur(mp3)
            SEGMENTS[i] = (shot, text, dur)
            print(f"  [{i:02d}] {shot}: {dur:.1f}s cached", flush=True)
            continue
        asyncio.run(_gen_tts(text, mp3))
        dur = _audio_dur(mp3)
        SEGMENTS[i] = (shot, text, dur)
        print(f"  [{i:02d}] {shot}: {dur:.1f}s  {text[:25]}...", flush=True)
        time.sleep(0.5)
    total = sum(s[2] for s in SEGMENTS)
    print(f"  Total audio: {total:.1f}s", flush=True)


# ═══════════════════════════════════════════════════════
#  Phase 2: Screenshots via Playwright
# ═══════════════════════════════════════════════════════
def _click_sidebar(page, keyword: str) -> bool:
    links = page.query_selector_all('[data-testid="stSidebar"] a')
    for link in links:
        txt = link.inner_text() or ""
        if keyword in txt:
            link.click()
            page.wait_for_timeout(3500)
            return True
    print(f"  WARNING: sidebar '{keyword}' not found")
    return False


def capture_screenshots():
    print(">>> [2/6] Playwright screenshots...")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": W, "height": H})
        page = ctx.new_page()

        # ── Login page ──
        page.goto(APP, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        try:
            page.locator('input[aria-label="用户名"]').fill("admin")
            page.wait_for_timeout(300)
            page.locator('input[aria-label="密码"]').fill("admin123")
            page.wait_for_timeout(500)
        except Exception as e:
            print(f"  login fill warn: {e}")
        page.screenshot(path=str(SHOTS / "01_login.png"))
        print("  [01_login] captured")

        # Click login
        try:
            page.locator('button[type="submit"]').first.click()
        except Exception:
            try:
                page.locator('[data-testid="stFormSubmitButton"]').first.click()
            except Exception as e:
                print(f"  login click warn: {e}")
        page.wait_for_timeout(6000)
        print("  logged in")

        # ── Upload page ──
        # Upload image
        try:
            page.locator('input[type="file"]').set_input_files(TEST_IMG)
            page.wait_for_timeout(2500)
        except Exception as e:
            print(f"  upload image warn: {e}")
        # Fill some form fields
        for label, val in [("监火人", "张三"), ("作业区域", "A区焊接车间")]:
            try:
                page.locator(f'input[aria-label="{label}"]').fill(val)
                page.wait_for_timeout(200)
            except Exception:
                pass
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "02_upload.png"))
        print("  [02_upload] captured")

        # Submit form → navigate to agents
        try:
            page.locator('button[type="submit"]').last.click()
        except Exception:
            try:
                page.locator('[data-testid="stFormSubmitButton"]').first.click()
            except Exception as e:
                print(f"  submit warn: {e}")
        page.wait_for_timeout(5000)
        print("  submitted, on agents page")

        # ── Agents page (before run) ──
        page.screenshot(path=str(SHOTS / "03_agents.png"))
        print("  [03_agents] captured")

        # Click run button
        for sel in ['[data-testid="stBaseButton-primary"]', 'button:has-text("运行")', 'button:has-text("开始")']:
            try:
                btn = page.locator(sel).first
                btn.click(timeout=3000)
                break
            except Exception:
                continue
        page.wait_for_timeout(15000)  # wait for agents to complete
        page.screenshot(path=str(SHOTS / "03b_agents_result.png"))
        print("  [03b_agents_result] captured")

        # ── Report page ──
        _click_sidebar(page, "工单")
        page.screenshot(path=str(SHOTS / "04_report.png"))
        print("  [04_report] captured")

        # ── History page ──
        _click_sidebar(page, "历史")
        page.screenshot(path=str(SHOTS / "05_history.png"))
        print("  [05_history] captured")

        # ── Realtime page ──
        _click_sidebar(page, "实时")
        page.wait_for_timeout(2000)
        page.screenshot(path=str(SHOTS / "06_realtime.png"))
        print("  [06_realtime] captured")

        # ── Admin page ──
        _click_sidebar(page, "管理")
        page.screenshot(path=str(SHOTS / "07_admin.png"))
        print("  [07_admin] captured")

        # ── Diagnostics page ──
        _click_sidebar(page, "自检")
        page.screenshot(path=str(SHOTS / "08_diag.png"))
        print("  [08_diag] captured")

        browser.close()
    print("  screenshots done")


# ═══════════════════════════════════════════════════════
#  Phase 3: PIL subtitle rendering + ending card
# ═══════════════════════════════════════════════════════
def _wrap_text(text: str, font, max_w: int, draw) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        test = cur + ch
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _render_subtitle(screenshot_path: Path, text: str, output_path: Path):
    img = Image.open(screenshot_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 20)
    max_w = img.width - 100
    lines = _wrap_text(text, font, max_w, draw)
    line_h = 28
    block_h = line_h * len(lines) + 12
    y0 = img.height - block_h - 12
    # Semi-transparent black bar
    draw.rectangle([0, y0 - 6, img.width, img.height], fill=(0, 0, 0, 150))
    # White text centered
    y = y0 + 6
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (img.width - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)
        y += line_h
    img.convert("RGB").save(str(output_path), "PNG")


def make_end_card():
    img = Image.new("RGB", (W, H), color=(13, 20, 35))
    draw = ImageDraw.Draw(img)
    f1 = ImageFont.truetype(FONT_PATH, 44)
    f2 = ImageFont.truetype(FONT_PATH, 22)
    title = "智护工地 · 施工安全智能体"
    sub = "感谢观看"
    for txt, font, y, color in [(title, f1, 290, (255, 255, 255)), (sub, f2, 370, (100, 200, 255))]:
        bbox = draw.textbbox((0, 0), txt, font=font)
        x = (W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), txt, fill=color, font=font)
    img.save(str(SHOTS / "09_end.png"), "PNG")
    print("  [09_end] ending card created")


def render_all_subtitles():
    print(">>> [3/6] PIL subtitle rendering...")
    make_end_card()
    for i, (shot, text, dur) in enumerate(SEGMENTS):
        src = SHOTS / f"{shot}.png"
        if not src.exists():
            print(f"  WARNING: {src} missing, using end card")
            src = SHOTS / "09_end.png"
        dst = TMP / f"frame_{i:03d}.png"
        _render_subtitle(src, text, dst)
    print(f"  rendered {len(SEGMENTS)} frames")


# ═══════════════════════════════════════════════════════
#  Phase 4: Audio concatenation
# ═══════════════════════════════════════════════════════
def concat_audio():
    print(">>> [4/6] Audio concatenation...")
    n = len(SEGMENTS)
    inputs = []
    for i in range(n):
        inputs.extend(["-i", str(TMP / f"audio_{i:03d}.mp3")])
    filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    full_audio = DEMO / "full_audio.mp3"
    subprocess.run(
        [FFMPEG, "-y"] + inputs +
        ["-filter_complex", filt, "-map", "[out]",
         "-c:a", "libmp3lame", "-b:a", "128k", str(full_audio)],
        check=True, capture_output=True)
    dur = _audio_dur(full_audio)
    print(f"  full_audio.mp3: {dur:.1f}s")
    return full_audio


# ═══════════════════════════════════════════════════════
#  Phase 5: Video clips + concat + mux audio
# ═══════════════════════════════════════════════════════
def build_video(full_audio: Path):
    print(">>> [5/6] Building video...")
    n = len(SEGMENTS)

    # 5a. Individual clips
    for i, (shot, text, dur) in enumerate(SEGMENTS):
        frame = TMP / f"frame_{i:03d}.png"
        clip = TMP / f"clip_{i:03d}.mp4"
        subprocess.run(
            [FFMPEG, "-y", "-loop", "1", "-t", f"{dur:.3f}",
             "-i", str(frame),
             "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
             "-pix_fmt", "yuv420p", "-r", "24", "-vf", f"scale={W}:{H}",
             str(clip)],
            check=True, capture_output=True)
    print(f"  created {n} clips")

    # 5b. Concat clips
    clips_txt = TMP / "clips.txt"
    with open(clips_txt, "w") as f:
        for i in range(n):
            f.write(f"file 'clip_{i:03d}.mp4'\n")
    combined = TMP / "combined.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(clips_txt),
         "-c", "copy", str(combined)],
        check=True, capture_output=True, cwd=str(TMP))
    print("  concatenated clips")

    # 5c. Mux audio
    demo = DEMO / "demo.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-i", str(combined), "-i", str(full_audio),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
         "-shortest", "-movflags", "+faststart", str(demo)],
        check=True, capture_output=True)
    dur = _audio_dur(demo)
    sz = demo.stat().st_size / 1024 / 1024
    print(f"  demo.mp4: {dur:.1f}s, {sz:.1f}MB")
    return demo


# ═══════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════
def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    gen_tts_all()
    capture_screenshots()
    render_all_subtitles()
    full_audio = concat_audio()
    demo = build_video(full_audio)

    # Save SRT for reference
    srt = DEMO / "subtitles.srt"
    cur = 0.0
    with open(srt, "w", encoding="utf-8") as f:
        for i, (shot, text, dur) in enumerate(SEGMENTS):
            start, end = cur, cur + dur
            cur = end
            h1, m1, s1 = int(start // 3600), int(start % 3600 // 60), start % 60
            h2, m2, s2 = int(end // 3600), int(end % 3600 // 60), end % 60
            f.write(f"{i+1}\n{h1:02d}:{m1:02d}:{s1:05.2f} --> {h2:02d}:{m2:02d}:{s2:05.2f}\n{text}\n\n")

    print(f"\n>>> DONE: {demo}")
    print(f">>> Screenshots: {SHOTS}")
    print(f">>> SRT: {srt}")


if __name__ == "__main__":
    main()
