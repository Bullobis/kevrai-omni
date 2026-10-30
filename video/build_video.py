#!/usr/bin/env python3
"""Code-generated 60s vertical promo video for Kevrai Omni (no AI assets).

All visuals are drawn with ffmpeg lavfi / drawtext / zoompan over solid dark
backgrounds; screenshots are the real renderer captures. Audio is synthesized
with ffmpeg sine oscillators (royalty-free).
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
VIDEO = ROOT
W, H, FPS, DUR = 1080, 1920, 30, 10
BG = "0x0e0f12"
FG = "0xe6e7ea"
MUT = "0x9aa0ab"
AC = "0x7ee2a8"
BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

MARKET = str(REPO / "website/assets/market.png")
CHAT = str(REPO / "website/assets/chat.png")


def run(cmd: list[str], tag: str) -> None:
    print(f"== {tag} ==")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise SystemExit(f"segment {tag} failed")


def base(vf_extra: str = "") -> str:
    return vf_extra


# 1) Opening ---------------------------------------------------------------
def seg1() -> None:
    vf = (
        f"drawtext=fontfile={BOLD}:text='Kevrai Omni':fontsize=104:fontcolor={FG}:"
        f"x=(w-text_w)/2:y=h/2-140:alpha='if(lt(t,1.0),t/1.0,1)',"
        f"drawtext=fontfile={REG}:text='一键本地 AI 工作站':fontsize=46:fontcolor={AC}:"
        f"x=(w-text_w)/2:y=h/2+20:alpha='if(lt(t,1.4),max(0,(t-0.4)/1.0),1)',"
        f"drawtext=fontfile={REG}:text='本地运行 · 数据不上传 · 无需订阅':fontsize=32:fontcolor={MUT}:"
        f"x=(w-text_w)/2:y=h/2+110:alpha='if(lt(t,1.8),max(0,(t-0.8)/1.0),1)'"
    )
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={DUR}:r={FPS}",
        "-vf", vf, "-t", str(DUR), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(VIDEO / "seg1.mp4"),
    ], "seg1 opening")


# 2) Positioning -----------------------------------------------------------
def seg2() -> None:
    lines = [
        ("全部跑在你自己的电脑上", BOLD, 62, FG, -360),
        ("大语言模型 · 语音合成", REG, 42, FG, -180),
        ("图像生成 · 视频生成", REG, 42, FG, -100),
        ("3D · 音频 · 超分辨率", REG, 42, FG, -20),
        ("不依赖云端，不按年收费", REG, 36, AC, 120),
    ]
    parts = []
    for i, (txt, f, size, col, y) in enumerate(lines):
        a = f"if(lt(t,{0.6+i*0.35}),max(0,(t-{i*0.35})/0.6),1)"
        parts.append(
            f"drawtext=fontfile={f}:text='{txt}':fontsize={size}:fontcolor={col}:"
            f"x=(w-text_w)/2:y=h/2{y:+d}:alpha='{a}'"
        )
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={DUR}:r={FPS}",
        "-vf", ",".join(parts), "-t", str(DUR), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(VIDEO / "seg2.mp4"),
    ], "seg2 positioning")


# 3/4) Screenshot segments ------------------------------------------------
def seg_shot(out: str, title: str, sub: str, img: str, tag: str) -> None:
    # slow push-in on the screenshot; title on top, subtitle below
    vf = (
        f"zoompan=z='min(zoom+0.0006,1.10)':d={DUR*FPS}:x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
        f"drawtext=fontfile={BOLD}:text='{title}':fontsize=64:fontcolor={FG}:"
        f"x=(w-text_w)/2:y=240:alpha='if(lt(t,0.8),t/0.8,1)',"
        f"drawtext=fontfile={REG}:text='{sub}':fontsize=34:fontcolor={MUT}:"
        f"x=(w-text_w)/2:y=H-320:alpha='if(lt(t,1.2),max(0,(t-0.3)/0.9),1)'"
    )
    # build a dark frame first, overlay screenshot scaled to ~980 wide centered
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={DUR}:r={FPS}",
        "-loop", "1", "-t", str(DUR), "-i", img,
        "-filter_complex",
        f"[1:v]scale=980:-1,zoompan=z='min(zoom+0.0006,1.10)':d={DUR*FPS}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=980x613:fps={FPS}[shot];"
        f"[0:v][shot]overlay=(W-w)/2:(H-h)/2-40,"
        f"drawtext=fontfile={BOLD}:text='{title}':fontsize=64:fontcolor={FG}:"
        f"x=(w-text_w)/2:y=220:alpha='if(lt(t,0.8),t/0.8,1)',"
        f"drawtext=fontfile={REG}:text='{sub}':fontsize=34:fontcolor={MUT}:"
        f"x=(w-text_w)/2:y=H-300:alpha='if(lt(t,1.2),max(0,(t-0.3)/0.9),1)'",
        "-t", str(DUR), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(VIDEO / out),
    ], tag)


# 5) Feature matrix -------------------------------------------------------
def seg5() -> None:
    feats = [
        ("LLM 对话", 0), ("语音合成", 1), ("图像生成", 2),
        ("视频生成", 3), ("3D 模型", 4), ("音频 / 超分", 5),
    ]
    col_center = [240, 540, 840]
    row_y = [-180, 40]
    parts = [
        f"drawtext=fontfile={BOLD}:text='一个应用，多种能力':fontsize=58:fontcolor={FG}:"
        f"x=(w-text_w)/2:y=260:alpha='if(lt(t,0.8),t/0.8,1)'"
    ]
    for label, idx in feats:
        cx = col_center[idx % 3]
        y = row_y[idx // 3]
        a = f"if(lt(t,{0.5+idx*0.25}),max(0,(t-{idx*0.25})/0.5),1)"
        parts.append(
            f"drawtext=fontfile={REG}:text='{label}':fontsize=38:fontcolor={AC}:"
            f"x={cx}-text_w/2:y=h/2{y:+d}:alpha='{a}'"
        )
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={DUR}:r={FPS}",
        "-vf", ",".join(parts), "-t", str(DUR), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(VIDEO / "seg5.mp4"),
    ], "seg5 matrix")


# 6) Closing --------------------------------------------------------------
def seg6() -> None:
    parts = [
        f"drawtext=fontfile={BOLD}:text='现在就开始':fontsize=80:fontcolor={FG}:"
        f"x=(w-text_w)/2:y=h/2-300:alpha='if(lt(t,0.8),t/0.8,1)'",
        f"drawtext=fontfile={REG}:text='下载 · 装引擎 · 选模型':fontsize=40:fontcolor={MUT}:"
        f"x=(w-text_w)/2:y=h/2-180:alpha='if(lt(t,1.2),max(0,(t-0.4)/0.8),1)'",
        f"drawtext=fontfile={BOLD}:text='kevrai-omni.pages.dev':fontsize=44:fontcolor={AC}:"
        f"x=(w-text_w)/2:y=h/2-40:alpha='if(lt(t,1.6),max(0,(t-0.8)/0.8),1)'",
        f"drawtext=fontfile={REG}:text='github.com/Bullobis/kevrai-omni':fontsize=32:fontcolor={MUT}:"
        f"x=(w-text_w)/2:y=h/2+60:alpha='if(lt(t,2.0),max(0,(t-1.2)/0.8),1)'",
    ]
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={DUR}:r={FPS}",
        "-vf", ",".join(parts), "-t", str(DUR), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(VIDEO / "seg6.mp4"),
    ], "seg6 closing")


def concat() -> None:
    listfile = VIDEO / "list.txt"
    listfile.write_text("".join(f"file 'seg{i}.mp4'\n" for i in range(1, 7)))
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(VIDEO / "video_only.mp4"),
    ], "concat video")


if __name__ == "__main__":
    seg1(); seg2()
    seg_shot("seg3.mp4", "模型市场", "双源搜索，量化档按需下载", MARKET, "seg3 market")
    seg_shot("seg4.mp4", "本地对话", "选模型即开聊，不联网上传", CHAT, "seg4 chat")
    seg5(); seg6(); concat()
    print("video_only.mp4 done")
