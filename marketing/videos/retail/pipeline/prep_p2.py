#!/usr/bin/env python3
"""v2: resize the captures in shots_p2/ to 2x their on-video size (Lanczos) -> build_p2/img, and resolve every outline / camera rect
([shot, target, pad] -> CSS px from shots_p2/<shot>.rects.json) -> build_p2/p2_resolved.json (read by frames_p2.js and render_p2.py)."""
import json, os
from PIL import Image
C = json.load(open("p2.json")); L = C["layout"]
os.makedirs("build_p2/img", exist_ok=True)
S = L["screen_scale"]; dw, dh = round(1024 * S), round(642 * S); P = L["phone"]
used = set()
for ln in C["lines"].values():
    for g in ln.get("segs", []):
        for k in ("desk", "phone"):
            if g.get(k): used.add(g[k])
for sid in sorted(used):
    src, out = f"shots_p2/{sid}.png", f"build_p2/img/{sid}.png"
    w, h = ((P["w"], P["h"]) if sid.endswith("_phone") else (dw, dh))
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(src):
        Image.open(src).convert("RGB").resize((2 * w, 2 * h), Image.LANCZOS).save(out)


def rect(r):
    if isinstance(r[0], str):
        shot, name, pad = r; x, y, w, h = json.load(open(f"shots_p2/{shot}.rects.json"))[name]
        return [x - pad, y - pad, w + 2 * pad, h + 2 * pad]
    return list(r)


for lid, ln in C["lines"].items():
    for h in ln.get("highlights", []): h["rect_px"] = rect(h["rect"])
    for k in ("push", "slow_push"):
        if ln.get(k): ln[k]["target_px"] = rect(ln[k]["target"])
json.dump(C, open("build_p2/p2_resolved.json", "w"), ensure_ascii=False, indent=1)
print("prep_p2 ok:", len(used), "captures", dw, dh)
