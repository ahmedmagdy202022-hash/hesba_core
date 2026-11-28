#!/usr/bin/env python3
"""Contact sheet: one frame every 3 s (t = 1.5, 4.5, ...), each tile 480x270 labelled with its true time, 4 per row.
usage: python3 contact.py [video] [out.png]"""
import os, subprocess, sys, tempfile
from PIL import Image, ImageDraw
v = sys.argv[1] if len(sys.argv) > 1 else "hesba-promo-draft.mp4"; out = sys.argv[2] if len(sys.argv) > 2 else "check/contact.png"
D = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", v]))
ts = [1.5 + 3 * k for k in range(int((D - 1.5) // 3) + 1)]
tiles = []
with tempfile.TemporaryDirectory() as tmp:
    for t in ts:
        f = f"{tmp}/{t:.1f}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", v, "-frames:v", "1", "-vf", "scale=480:270:flags=lanczos", f], check=True)
        im = Image.open(f).convert("RGB"); d = ImageDraw.Draw(im); d.rectangle([0, 0, 64, 22], fill=(0, 0, 0)); d.text((6, 5), f"{t:5.1f}s", fill=(255, 255, 255))
        tiles.append(im)
cols = 4; rows = (len(tiles) + cols - 1) // cols; P = 6
sheet = Image.new("RGB", (cols * 480 + (cols + 1) * P, rows * 270 + (rows + 1) * P), (255, 255, 255))
for k, im in enumerate(tiles): sheet.paste(im, (P + (k % cols) * (480 + P), P + (k // cols) * (270 + P)))
os.makedirs(os.path.dirname(out) or ".", exist_ok=True); sheet.save(out); print(out, len(tiles), "tiles")
