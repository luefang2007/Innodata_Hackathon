#!/usr/bin/env python
"""Export social/poster.html with a headless Edge or Chrome.

    python social/render.py                      # every style, English and Chinese: poster_<style>_<lang>.jpg, 2160 x 2700
    python social/render.py --styles ink lupi    # only these styles
    python social/render.py --scale 1            # 1080 x 1350

The paper grain makes a PNG of this poster about 8 MB, so the screenshot is saved as a JPEG when Pillow is
installed (about 2 MB, right for LinkedIn and WeChat); without Pillow the PNG is kept.
The browser gets a throw-away profile next to the output, so a running Edge or Chrome is left alone.
"""
from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 1080, 1350
STYLES = ["haze", "roman", "gothic", "ink", "lupi", "cloth", "stone"]     # see the header of poster.html
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def browser() -> str:
    for p in BROWSERS:
        if Path(p).exists():
            return p
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    sys.exit("No Edge or Chrome found: open social/poster.html in a browser and screenshot it instead.")


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def wait_for(path: Path, seconds: float = 60) -> bool:
    """The browser can return before it has written the file: wait until the file is there and complete."""
    end, last = time.time() + seconds, -1
    while time.time() < end:
        size = path.stat().st_size if path.exists() else -1
        if size > 0 and size == last:
            return True
        last = size
        time.sleep(0.5)
    return False


def to_jpeg(png: Path) -> Path:
    """Save the screenshot as a JPEG next to it and drop the PNG; return the PNG itself if Pillow is missing."""
    try:
        from PIL import Image
    except ImportError:
        return png
    jpg = png.with_suffix(".jpg")
    with Image.open(png) as im:
        im.convert("RGB").save(jpg, "JPEG", quality=95, subsampling=0, optimize=True, progressive=True)
    png.unlink()
    return jpg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scale", type=float, default=2, help="device pixels per CSS pixel (default 2)")
    ap.add_argument("--out", type=Path, default=HERE)
    ap.add_argument("--styles", nargs="+", default=STYLES, choices=STYLES, metavar="STYLE",
                    help="which styles to export (default: all of " + ", ".join(STYLES) + ")")
    a = ap.parse_args()
    exe = browser()
    a.out.mkdir(parents=True, exist_ok=True)
    profile = a.out / ".render-profile"
    status = 0
    try:
        for style in a.styles:
            for lang in ("en", "zh"):
                png = a.out / f"poster_{style}_{lang}.png"
                for old in (png, png.with_suffix(".jpg")):
                    old.unlink(missing_ok=True)
                url = (HERE / "poster.html").as_uri() + f"?style={style}&lang={lang}"
                subprocess.run(
                    [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--disable-lcd-text",
                     "--no-default-browser-check", f"--user-data-dir={profile}",
                     f"--force-device-scale-factor={a.scale}", f"--window-size={W},{H}",
                     f"--screenshot={png}", url],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
                if not wait_for(png):
                    print(f"{png.name}: the browser wrote no file")
                    status = 1
                    continue
                size = png_size(png)
                want = (round(W * a.scale), round(H * a.scale))
                out = to_jpeg(png)
                print(f"{out.name}: {size[0]} x {size[1]}, {out.stat().st_size / 1e6:.2f} MB"
                      + ("" if size == want else f"   (expected {want[0]} x {want[1]})")
                      + ("" if out.suffix == ".jpg" else "   (PNG kept: install Pillow for a smaller JPEG)"))
    finally:
        for _ in range(10):                      # the browser may hold the profile for a moment after it exits
            shutil.rmtree(profile, ignore_errors=True)
            if not profile.exists():
                break
            time.sleep(0.5)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
