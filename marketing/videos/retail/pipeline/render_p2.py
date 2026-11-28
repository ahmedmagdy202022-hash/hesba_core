#!/usr/bin/env python3
"""v2 promo: render the layers from frames_p2.js + voice (vo3/lines) + ducked music.

usage: python3 render_p2.py OUT.mp4 [--jobs 4] [--frames t1,t2,...] [--tl]
  --frames  write check PNGs only (OUT is a filename prefix)        --tl  print the timeline and checks, then stop

Timeline: one scene per narration line (p1..p9). Scene i runs T_i .. T_i + L_i; scene i+1 starts XF (0.8 s) before scene i ends.
  L = max(MIN_LEN, LEAD + clip + TAIL)   (LEAD 0.6: clip start after the scene start; TAIL 0.7 -> about 0.5 s of air between lines)
  push lines: L >= LEAD + push + PUSH_DUR + 1.0 + XF;   p8: L >= LEAD + clip + P8_HOLD + XF;   last: L = LEAD + clip + END_HOLD + END_FADE
Transitions never double text: every change is a dip. Between lines the old frame dissolves (0.4 s) into the new scene's bare frame
(background + device bodies, no screens, no text; plain background when the old scene ends zoomed in), then the new content dissolves in (0.4 s). Inside a line a screen change dips the
screen to the app's blank page and back (DIP, 0.8 s, or the seg's own 'dip'); a card change dips the whole device group.
Push-ins: pure zoom about a fixed point (smootherstep), end box solved so the target sits inside the safe area below the caption band.
The phone fades out (PHONE_FADE) before the push would crop it at the frame edge. The band turns opaque before the screen reaches it.
Audio: as v1 (voice bus -16 LUFS through a limiter, music ducked 12 dB under speech, final ~-16 LUFS, TP <= -1.5 dBTP)."""
import json, math, os, re, subprocess, sys
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
C = json.load(open("build_p2/p2_resolved.json")); script = json.load(open("script_p2.json", encoding="utf-8"))
META = json.load(open("build_p2/layers/meta.json"))
W, H, FPS, SR = 1920, 1080, 30, 48000
XF, LEAD, TAIL, DIP, MIN_LEN, XFADE = C["xf"], C["lead"], C["tail"], C["dip"], C["min_len"], C["xfade"]
PUSH, HLF, PFADE, BAND = C["push_dur"], C["hl_fade"], C["phone_fade"], C["band"]
CRF = os.environ.get("CRF", "18")
MUSIC = os.path.abspath(os.path.join(HERE, "../voice-pick/music-elegant.mp3"))
BED_LUFS, VOICE_LUFS, DUCK_DB, TARGET_LUFS, TP_MAX = -20.0, -16.0, 12.0, -16.0, -1.5
NAVY = np.array([9, 40, 81], np.float32); RAISED = np.array([20, 56, 107], np.float32); TEAL = np.array([2, 174, 183], np.float32)
GOLD = np.array([242, 213, 142], np.float32); BANDC = NAVY * 0.86
LY = "build_p2/layers"; M2 = 600
G = META["geo"]


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def dur(f):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).decode())


def smooth(x):
    x = min(1.0, max(0.0, x)); return x * x * (3 - 2 * x)


def smoother(x):
    x = min(1.0, max(0.0, x)); return x * x * x * (x * (6 * x - 15) + 10)


# ------------------------------------------------------------------ timeline
TL, t = [], 0.0
for k, s in enumerate(script):
    c = C["lines"][s["id"]]; vd = dur(s["vo"])
    L = max(MIN_LEN, LEAD + vd + TAIL)
    if c.get("push"): L = max(L, LEAD + c["push"]["at"] + PUSH + 1.0 + XF)
    if s["id"] == "p8": L = max(L, LEAD + vd + C["p8_hold"] + XF)
    for g in c["segs"]:
        if g.get("min_read"): L = max(L, LEAD + g["at"] + g["min_read"] + XF, LEAD + vd + 1.5 + XF)   # card fully readable >= min_read s, and >= 1.5 s after the clip
    if k == len(script) - 1: L = LEAD + vd + C["end_hold"] + C["end_fade"]
    TL.append({"id": s["id"], "caption": s["caption"], "voice_file": s["vo"], "start": round(t, 3), "len": round(L, 3), "end": round(t + L, 3),
               "voice_start": round(t + LEAD, 3), "voice_dur": round(vd, 3), "voice_end": round(t + LEAD + vd, 3)})
    t += L - XF
TOTAL = TL[-1]["end"]; NF = int(round(TOTAL * FPS)); BY = {e["id"]: e for e in TL}


def tau_of(x): return LEAD + x     # clip time -> scene time


# ------------------------------------------------------------------ segments: what is on the devices / cards at scene time tau
def segs(lid):
    out = []; prev_card = False
    for g in C["lines"][lid]["segs"]:
        dev_change = g["at"] and not g.get("card") and not prev_card   # a screen change inside the devices: direct crossfade
        d = g.get("dip", XFADE if dev_change else DIP); end = tau_of(g["at"]) if g["at"] else 0.0
        out.append(dict(g, t0=(end - d) if g["at"] else -1.0, t1=end, h=d / 2, xfade=bool(dev_change)))
        prev_card = bool(g.get("card"))
    return out


SEGS = {e["id"]: segs(e["id"]) for e in TL}


def state(lid, tau):
    """alphas at tau: dev (device group), desk {shot: a}, phone {shot: a}, card {name: (a, t_start)}, band"""
    st = {"dev": 0.0, "desk": {}, "phone": {}, "card": {}, "hlw": {}, "band": 1.0 if C["lines"][lid].get("band", True) else 0.0}
    desk = phone = card = None; dev = 0.0
    for g in SEGS[lid]:
        if g["t0"] < 0:      # first segment: simply there
            if g.get("card"): card = g["card"]; st["card"][card] = (1.0, 0.0)
            else: desk, phone, dev = g.get("desk"), g.get("phone"), 1.0; st["desk"][desk] = 1.0; st["phone"][phone] = 1.0
            continue
        if tau < g["t0"]: break
        a_out = 1 - smooth((tau - g["t0"]) / g["h"]); a_in = smooth((tau - g["t0"] - g["h"]) / g["h"])
        if g.get("card"):                         # devices (or previous card) out, card in
            if dev: dev = a_out
            for kc in list(st["card"]): st["card"][kc] = (st["card"][kc][0] * a_out, st["card"][kc][1])
            if g.get("band_off"): st["band"] *= a_out
            card = g["card"]; st["card"][card] = (a_in, g["t0"] + g["h"])
        else:
            if card is not None and not dev:      # card out, devices in
                st["card"][card] = (st["card"][card][0] * a_out, st["card"][card][1]); dev = a_in
                desk, phone = g.get("desk", desk), g.get("phone", phone); st["desk"] = {desk: 1.0}; st["phone"] = {phone: 1.0}; card = None
            else:                                 # direct crossfade: the new screen is drawn over the old one, no blank frame
                a = smooth((tau - g["t0"]) / (g["t1"] - g["t0"]))
                if g.get("desk") and g["desk"] != desk:
                    st["desk"] = {desk: 1.0, g["desk"]: a} if tau < g["t1"] else {g["desk"]: 1.0}
                    st["hlw"][desk] = 1 - a; st["hlw"][g["desk"]] = a; desk = g["desk"]
                if g.get("phone") and g["phone"] != phone:
                    st["phone"] = {phone: 1.0, g["phone"]: a} if tau < g["t1"] else {g["phone"]: 1.0}; phone = g["phone"]
    st["dev"] = dev
    return st


# ------------------------------------------------------------------ camera
def comp_rect(r, on="desk"):
    ox, oy, sc = (G["phone"] if on == "phone" else G["screen"])[:3]
    return [ox + r[0] * sc, oy + r[1] * sc, r[2] * sc, r[3] * sc]


def solve_box(rect_px, max_zoom=None):
    tx, ty, tw, th = comp_rect(rect_px); T0, T1 = (tx, ty), (tx + tw, ty + th)
    lx, ly, lw, lh = G["laptop_box"]; Lx0, Ly0, Lx1, Ly1 = lx, ly, lx + lw, ly + lh
    mg, top = BAND["margin"], BAND["safe_top"]
    Z = min(max_zoom or C["max_zoom"], (W - 2 * mg) / tw, (H - top - mg) / th)
    for laptop in (True, False):
        z = Z
        while z > 1.05:
            bw, bh = W / z, H / z
            xlo, xhi = max(T1[0] - (W - mg) / z, 0), min(T0[0] - mg / z, W - bw)
            ylo, yhi = max(T1[1] - (H - mg) / z, 0), min(T0[1] - top / z, H - bh)
            if laptop: xlo, xhi, ylo, yhi = max(xlo, Lx0), min(xhi, Lx1 - bw), max(ylo, Ly0), min(yhi, Ly1 - bh)
            if xlo <= xhi and ylo <= yhi:
                return (min(max((T0[0] + T1[0]) / 2 - bw / 2, xlo), xhi), min(max((T0[1] + T1[1]) / 2 - ((top + H - mg) / 2) / z, ylo), yhi), z, laptop)
            z -= 0.01
    raise SystemExit("no camera box fits")


BOX = {lid: solve_box(c["push"]["target_px"], c["push"].get("max_zoom")) for lid, c in C["lines"].items() if c.get("push")}


def camera(lid, tau, L):
    c = C["lines"][lid]
    if lid in BOX:
        x0f, y0f, Z, _ = BOX[lid]; u = smoother((tau - tau_of(c["push"]["at"])) / PUSH)
        z = math.exp(math.log(Z) * u); bw, bh = W / z, H / z
        px, py = x0f / (W - W / Z), y0f / (H - H / Z); x0, y0 = px * (W - bw), py * (H - bh)
        return (x0, y0, x0 + bw, y0 + bh), z
    if c.get("slow_push"):
        tx, ty, tw, th = comp_rect(c["slow_push"]["target_px"]); fx, fy = tx + tw / 2, ty + th / 2
        z = 1 + (c["slow_push"]["zoom"] - 1) * smooth(tau / L)
        return (fx - fx / z, fy - fy / z, fx + (W - fx) / z, fy + (H - fy) / z), z
    return (0, 0, W, H), 1.0


def phone_out_box(box, z):
    x, y, w, h = G["phone_box"]; return ((x - box[0]) * z, (y - box[1]) * z, (x + w - box[0]) * z, (y + h - box[1]) * z)


def phone_inside(box, z, tol=2):
    a = phone_out_box(box, z); return a[0] >= -tol and a[1] >= -tol and a[2] <= W + tol and a[3] <= H + tol


PHONE_EXIT = {}
for e in TL:
    if e["id"] in BOX or C["lines"][e["id"]].get("slow_push"):
        for i in range(0, int(e["len"] * 100) + 1):
            tau = i / 100; box, z = camera(e["id"], tau, e["len"])
            if not phone_inside(box, z): PHONE_EXIT[e["id"]] = tau; break


def phone_alpha(lid, tau):
    te = PHONE_EXIT.get(lid)
    return 1.0 if te is None else 1 - smooth((tau - (te - PFADE - 0.05)) / PFADE)


# ------------------------------------------------------------------ pieces
yy, xx = np.mgrid[0:H + 80, 0:W + 80].astype(np.float32)


def make_bg():
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((x - 960) / 1000) ** 2 + ((y - 560) / 640) ** 2)
    vig = 1 - 0.42 * np.clip((r - 0.45) / 0.85, 0, 1) ** 1.6
    base = NAVY[None, None, :] * 0.86 * vig[..., None]
    g = np.exp(-(((xx - (W / 2 + 40 + 150)) / 760) ** 2 + ((yy - (H / 2 + 40 + 60)) / 470) ** 2))
    light = g[..., None] * ((RAISED - NAVY * 0.86)[None, None, :] * 1.05 + TEAL[None, None, :] * 0.04)
    return base + np.random.default_rng(7).normal(0, 2.2, (H, W, 1)).astype(np.float32), light


BG, LIGHT = make_bg()


def bg_frame(e, tau):
    f = tau / e["len"]; dx = int(round(-18 + 36 * f)); dy = int(round(-6 + 12 * f))
    return BG + LIGHT[40 + dy:40 + dy + H, 40 + dx:40 + dx + W]


ys = np.arange(H, dtype=np.float32)
BANDMASK = np.clip((BAND["soft_to"] - ys) / (BAND["soft_to"] - BAND["opaque_to"]), 0, 1)[:, None, None]
_cache = {}


def img(path, kind):
    if path not in _cache:
        if len(_cache) > 30: _cache.clear()
        if not os.path.exists(path): _cache[path] = None
        else:
            im = Image.open(path).convert("RGBA")
            if kind == "full":
                big = Image.new("RGBA", (im.width + 2 * M2, im.height + 2 * M2), (0, 0, 0, 0)); big.paste(im, (M2, M2)); im = big
            elif kind == "logo":
                pad = int(im.width * 0.1); big = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0)); big.paste(im, (pad, pad)); im = big
            else:
                big = Image.new("RGBA", (im.width, im.height + 160), (0, 0, 0, 0)); big.paste(im, (0, 80)); im = big
            _cache[path] = im
    return _cache[path]


_vc = {}


def view(path, box):
    key = (path, tuple(round(v, 3) for v in box))
    if key in _vc: return _vc[key]
    im = img(path, "full")
    if im is None: return None
    out = im.resize((W, H), Image.BICUBIC, box=(box[0] * 2 + M2, box[1] * 2 + M2, box[2] * 2 + M2, box[3] * 2 + M2))
    if len(_vc) > 16: _vc.clear()
    _vc[key] = out; return out


_tc = {}


def text_piece(path, bxy, dy=0.0):
    key = (path, round(dy, 2))
    if key in _tc: return _tc[key]
    im = img(path, "text"); bx, by, bw, bh = bxy; w, h = int(round(bw)), int(round(bh)) + 40; top = 40 - 2 * dy
    r = (im.resize((w, h), Image.BOX, box=(0, top, im.width, top + 2 * h)), int(round(bx)), int(round(by)) - 20)
    if len(_tc) > 40: _tc.clear()
    _tc[key] = r; return r


def logo_piece(path, bxy, s, sweep):
    im = img(path, "logo"); bx, by, bw, bh = bxy
    w, h = int(round(bw * 1.1)), int(round(bh * 1.1)); cw, ch = im.width / 2, im.height / 2; hw, hh = (w / 2) / s * 2, (h / 2) / s * 2
    out = im.resize((w, h), Image.LANCZOS, box=(cw - hw, ch - hh, cw + hw, ch + hh))
    if sweep is not None:
        a = np.asarray(out).astype(np.float32); v, u = np.mgrid[0:h, 0:w].astype(np.float32)
        k = np.exp(-((((u + 0.45 * v) / (w + 0.45 * h)) - sweep) / 0.07) ** 2)[..., None] * 0.55
        a[..., :3] = a[..., :3] + (GOLD - a[..., :3]) * k; out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")
    return out, int(round(bx + bw / 2 - w / 2)), int(round(by + bh / 2 - h / 2))


def put(canvas, piece, alpha):
    im, x, y = piece
    if im is None or alpha <= 0.001: return
    a = np.asarray(im).astype(np.float32)
    x0, y0 = max(0, x), max(0, y); x1, y1 = min(W, x + im.width), min(H, y + im.height)
    if x1 <= x0 or y1 <= y0: return
    sub = a[y0 - y:y1 - y, x0 - x:x1 - x]; al = sub[..., 3:4] / 255 * alpha
    canvas[y0:y1, x0:x1] = canvas[y0:y1, x0:x1] * (1 - al) + sub[..., :3] * al


def logo_anim(tau):
    e = smoother(tau / 1.2); sw = (tau - 1.2) / 1.0
    return e, 0.94 + 0.06 * e, (-0.25 + 1.5 * sw if 0 <= sw <= 1 else None)


def band_alpha(box):
    z = W / (box[2] - box[0]); top = (G["laptop_box"][1] - 40 - box[1]) * z
    return float(np.clip((BAND["opaque_to"] + 90 - top) / 60, 0, 1))


def hl_alpha(h, tau, st, ph_a):
    a = smooth((tau - tau_of(h["from"])) / HLF)
    if h.get("to") is not None: a *= 1 - smooth((tau - tau_of(h["to"])) / HLF)
    if h.get("shot"): a *= st["hlw"].get(h["shot"], st["desk"].get(h["shot"], 0.0))
    if h["on"] == "phone": a *= ph_a
    return a * st["dev"]


def has_card(e, tau): return any(a > 0.001 for a, _ in state(e["id"], tau)["card"].values())


def caption_piece(e):
    return text_piece(f"{LY}/{e['id']}/head.png", META["lines"][e["id"]]["head"]) if C["lines"][e["id"]].get("band", True) else None


def scene_frame(e, tau, bare=False, info=None, cap=True):
    lid = e["id"]; c = C["lines"][lid]; f = bg_frame(e, tau); st = state(lid, tau)
    box, z = camera(lid, tau, e["len"]); ph = phone_alpha(lid, tau)
    if st["dev"] > 0:
        put(f, (view(f"{LY}/lap.png", box), 0, 0), st["dev"])
        put(f, (view(f"{LY}/phone.png", box), 0, 0), st["dev"] * ph)
        if not bare:
            for sid, a in st["desk"].items(): put(f, (view(f"{LY}/shot_{sid}.png", box), 0, 0), a * st["dev"])
            for sid, a in st["phone"].items(): put(f, (view(f"{LY}/shot_{sid}.png", box), 0, 0), a * st["dev"] * ph)
            for k, h in enumerate(c.get("highlights", [])):
                a = hl_alpha(h, tau, st, ph)
                if a > 0: put(f, (view(f"{LY}/{lid}/hl{k}.png", box), 0, 0), a)
        if z > 1.0001:
            ba = band_alpha(box)
            if ba > 0: f = f * (1 - BANDMASK * ba) + BANDC[None, None, :] * (BANDMASK * ba)
            if info is not None: info["band"] = ba
        put(f, text_piece(f"{LY}/slogo.png", META["slogo"]), st["dev"])
    if not bare:
        for name, (a, t0) in st["card"].items():
            if a <= 0.001: continue
            m = META["cards"][name]; d = f"{LY}/cards/{name}"; lt = tau - max(0.0, t0)
            if "logo" in m:
                le, ls, lsw = logo_anim(lt) if name != "logo_big" or True else (1, 1, None)
                put(f, logo_piece(f"{d}/logo.png", m["logo"], ls, lsw), a * le)
            if "head" in m: put(f, text_piece(f"{d}/head.png", m["head"]), a)
            if "pill" in m: put(f, text_piece(f"{d}/pill.png", m["pill"]), a)
        if cap and st["band"] > 0 and c.get("band", True):
            put(f, text_piece(f"{LY}/{lid}/head.png", META["lines"][lid]["head"]), st["band"])
    if info is not None: info.update(box=box, z=z, phone=ph, st=st)
    return f


def frame(t):
    act = [e for e in TL if e["start"] - 1e-6 <= t < e["end"] - 1e-6] or [TL[-1]]
    if len(act) == 1:
        e = act[0]; f = scene_frame(e, t - e["start"])
    else:
        a, b = act[0], act[1]; x = (t - b["start"]) / XF; ta, tb = t - a["start"], t - b["start"]
        if has_card(a, ta) or has_card(b, tb) or camera(a["id"], a["len"], a["len"])[1] > 1.0005:
            # a card on either side, or the old scene ends zoomed in: dip through the plain navy background (old out, then new in)
            nb = bg_frame(b, 0.0)
            if x < 0.5: w = smooth(x / 0.5); f = scene_frame(a, ta) * (1 - w) + nb * w
            else: w = smooth((x - 0.5) / 0.5); f = nb * (1 - w) + scene_frame(b, tb) * w
        else:
            # devices to devices: the screens crossfade directly (no blank page); the band captions never overlap: old out, then new in
            w = smooth((x - 0.35) / 0.3); f = scene_frame(a, ta, cap=False) * (1 - w) + scene_frame(b, tb, cap=False) * w   # fast 0.24 s screen crossfade mid-transition
            put(f, caption_piece(a) or (None, 0, 0), (1 - smooth(x / 0.5)) * state(a["id"], ta)["band"])
            put(f, caption_piece(b) or (None, 0, 0), smooth((x - 0.5) / 0.5) * state(b["id"], tb)["band"])
    if t > TOTAL - C["end_fade"]: f = f + (BANDC - f) * smooth((t - (TOTAL - C["end_fade"])) / C["end_fade"])
    return np.clip(f + 0.5, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ checks and timeline
def checks():
    out, bad = [], 0
    mg, top = BAND["margin"], BAND["safe_top"]
    for e in TL:
        lid = e["id"]; c = C["lines"][lid]
        for i in range(0, int(e["len"] * 30) + 1):          # every frame of the scene
            tau = i / 30; box, z = camera(lid, tau, e["len"]); st = state(lid, tau); ph = phone_alpha(lid, tau)
            if st["dev"] * ph > 0.004 and not phone_inside(box, z): bad += 1; out.append(f"  {lid} tau {tau:.2f}: PHONE CROPPED while visible"); break
            if z > 1.0001:
                dtop = (G["laptop_box"][1] - box[1]) * z
                if dtop < BAND["soft_to"] and band_alpha(box) < 0.999: bad += 1; out.append(f"  {lid} tau {tau:.2f}: band not opaque"); break
            for k, h in enumerate(c.get("highlights", [])):
                if hl_alpha(h, tau, st, ph) <= 0.004: continue
                r = META["lines"][lid]["hl"][k]; o = ((r[0] - box[0]) * z, (r[1] - box[1]) * z, (r[0] + r[2] - box[0]) * z, (r[1] + r[3] - box[1]) * z)
                if not (o[0] >= 0 and o[2] <= W and o[1] >= BAND["soft_to"] and o[3] <= H): bad += 1; out.append(f"  {lid} tau {tau:.2f}: outline {k} cut / under band {[round(v) for v in o]}"); break
    return out, bad


def cue_table():
    rows = []
    for e in TL:
        c = C["lines"][e["id"]]; T = e["start"]; cues = []
        for g in SEGS[e["id"]][1:]:
            what = g.get("card") or "/".join(x for x in (g.get("desk"), g.get("phone")) if x)
            cues.append({"what": f"screen -> {what}", "dip": [round(T + g["t0"], 2), round(T + g["t1"], 2)]})
        if c.get("push"): cues.append({"what": "push-in", "t": [round(T + tau_of(c["push"]["at"]), 2), round(T + tau_of(c["push"]["at"]) + PUSH, 2)]})
        if c.get("slow_push"): cues.append({"what": f"slow push x{c['slow_push']['zoom']}", "t": [T, e["end"]]})
        if e["id"] in PHONE_EXIT: te = T + PHONE_EXIT[e["id"]]; cues.append({"what": "phone fades out (before the push would crop it)", "t": [round(te - PFADE - 0.05, 2), round(te - 0.05, 2)]})
        for k, h in enumerate(c.get("highlights", [])):
            cues.append({"what": f"outline {k} ({h.get('shot') or h['on']})", "on": round(T + tau_of(h["from"]), 2), "full": round(T + tau_of(h["from"]) + HLF, 2),
                         "off": round(T + tau_of(h["to"]), 2) if h.get("to") is not None else None})
        e["cues"] = cues; e["words"] = c.get("words", "")
        e["caption_full"] = [round(T + XF, 2), round(e["end"] - XF, 2)]
        rows.append(e)
    return rows


def print_tl():
    print(f"timeline v2: {len(TL)} scenes, {TOTAL:.2f} s, {NF} frames")
    for e in cue_table():
        print(f"  {e['id']:<4} {e['start']:6.2f}-{e['end']:6.2f} ({e['len']:.2f})  voice {e['voice_start']:.2f}-{e['voice_end']:.2f}  caption fully on {e['caption_full'][0]:.2f}-{e['caption_full'][1]:.2f} ({e['caption_full'][1] - e['caption_full'][0]:.2f} s)")
        for q in e["cues"]: print("        ", q)
    for lid, (x0, y0, z, lap) in BOX.items():
        box, zz = camera(lid, 99, BY[lid]["len"]); top = (G["laptop_box"][1] - box[1]) * zz
        print(f"  push {lid}: zoom {z:.2f}{'' if lap else ' (laptop clamp relaxed)'}; laptop top edge at y {top:.0f} (band opaque to {BAND['opaque_to']}, soft to {BAND['soft_to']})")


# ------------------------------------------------------------------ video + audio (as v1)
def encode_chunk(args):
    i0, i1, path = args
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                          "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-c:v", "libx264", "-crf", CRF, "-preset", "medium",
                          "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", path], stdin=subprocess.PIPE)
    for i in range(i0, i1): p.stdin.write(frame(i / FPS).tobytes())
    p.stdin.close(); p.wait(); return path


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: print(" ".join(map(str, cmd))[:500]); print(r.stderr[-2000:]); sys.exit(1)
    return r.stderr


def ebur(path):
    o = run(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"]); o = o[o.rindex("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", o).group(1)), float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", o).group(1))


def read_f32(path, ch):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", str(ch), "-ar", str(SR), "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, ch).copy()


def write_wav(path, x):
    tmp = path + ".f32"; x.astype(np.float32).tofile(tmp)
    run(["ffmpeg", "-y", "-f", "f32le", "-ar", str(SR), "-ac", str(x.shape[1]), "-i", tmp, "-c:a", "pcm_f32le", path]); os.remove(tmp)


def audio(out_wav):
    A = "build_p2/audio"; os.makedirs(A, exist_ok=True); N = int(round(TOTAL * SR))
    src = dur(MUSIC); MXF = 4.0; loops = max(1, math.ceil((TOTAL + 1 - MXF) / (src - MXF)))
    ins, afc, prev = [], [], "[0:a]"
    for i in range(loops): ins += ["-i", MUSIC]
    for i in range(1, loops): afc.append(f"{prev}[{i}:a]acrossfade=d={MXF}[m{i}]"); prev = f"[m{i}]"
    afc.append(f"{prev}aresample={SR},aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=duration={TOTAL:.3f},afade=t=in:d=1.5,afade=t=out:st={TOTAL - 3.0:.3f}:d=3.0[a]")
    run(["ffmpeg", "-y", *ins, "-filter_complex", ";".join(afc), "-map", "[a]", "-c:a", "pcm_f32le", f"{A}/music_raw.wav"])
    ml, _ = ebur(f"{A}/music_raw.wav")
    music = read_f32(f"{A}/music_raw.wav", 2)[:N] * 10 ** ((BED_LUFS - ml) / 20); music = np.pad(music, ((0, max(0, N - len(music))), (0, 0)))
    bus = np.zeros(N, np.float32)
    for e in TL:
        x = read_f32(e["voice_file"], 1)[:, 0]; i0 = int(round(e["voice_start"] * SR)); bus[i0:i0 + len(x)] += x[:N - i0]
    write_wav(f"{A}/voice_raw.wav", bus[:, None]); g = 0.0
    for it in range(5):
        run(["ffmpeg", "-y", "-i", f"{A}/voice_raw.wav", "-af", f"pan=stereo|c0=c0|c1=c0,volume={g:.3f}dB,alimiter=limit=0.75:attack=5:release=80:level=0", "-c:a", "pcm_f32le", f"{A}/voice_bus.wav"])
        lv, tp = ebur(f"{A}/voice_bus.wav"); print(f"  voice bus pass {it}: {g:+.2f} dB -> {lv:.2f} LUFS, TP {tp:.2f}")
        if abs(lv - VOICE_LUFS) < 0.15: break
        g += VOICE_LUFS - lv
    vb = read_f32(f"{A}/voice_bus.wav", 2)[:N]; vb = np.pad(vb, ((0, N - len(vb)), (0, 0)))
    blocks = []
    for e in TL:
        a, b = e["voice_start"], e["voice_end"]
        if blocks and a - blocks[-1][1] < 1.6: blocks[-1][1] = max(blocks[-1][1], b)
        else: blocks.append([a, b])
    tt = np.arange(N) / SR; duck = np.zeros(N)
    for a, b in blocks:
        d0, d1, u0, u1 = a - 0.5, a - 0.1, b + 0.25, b + 0.95; lo, hi = max(0, int(d0 * SR)), min(N, int(u1 * SR)); x = tt[lo:hi]
        env = np.where(x < d1, 0.5 - 0.5 * np.cos(np.pi * np.clip((x - d0) / (d1 - d0), 0, 1)), np.where(x <= u0, 1.0, 0.5 + 0.5 * np.cos(np.pi * np.clip((x - u0) / (u1 - u0), 0, 1))))
        duck[lo:hi] = np.maximum(duck[lo:hi], env)
    mix = music * (10 ** (-DUCK_DB * duck / 20))[:, None] + vb
    print(f"  ducking: {len(blocks)} blocks, -{DUCK_DB:g} dB")
    write_wav(f"{A}/mix_pre.wav", mix); g = 0.0
    for it in range(6):
        run(["ffmpeg", "-y", "-i", f"{A}/mix_pre.wav", "-af", f"volume={g:.3f}dB,alimiter=limit=0.75:attack=5:release=80:level=0", "-c:a", "pcm_f32le", out_wav])
        lv, tp = ebur(out_wav); print(f"  mix pass {it}: {g:+.2f} dB -> {lv:.2f} LUFS, TP {tp:.2f} dBTP")
        if abs(lv - TARGET_LUFS) < 0.3 and tp <= TP_MAX: break
        g += TARGET_LUFS - lv
    os.makedirs("out", exist_ok=True)   # the narration alone, on the video's timeline, levelled (-16 LUFS)
    run(["ffmpeg", "-y", "-i", f"{A}/voice_bus.wav", "-c:a", "libmp3lame", "-b:a", "192k", "out/hesba-retail-promo-v2-voice.mp3"])


if __name__ == "__main__":
    out = sys.argv[1]
    print_tl()
    lines, bad = checks()
    for ln in lines: print(ln)
    print("geometry checks:", "OK" if not bad else f"{bad} PROBLEM(S)")
    os.makedirs("build", exist_ok=True)
    json.dump({"total": round(TOTAL, 3), "scenes": cue_table(), "boxes": {k: list(v) for k, v in BOX.items()}}, open("build/timeline_v2.json", "w"), ensure_ascii=False, indent=1)
    if "--tl" in sys.argv: sys.exit(1 if bad else 0)
    if arg("--frames"):
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        for tv in arg("--frames").split(","): Image.fromarray(frame(float(tv))).save(f"{out}_{float(tv):06.2f}.png")
        sys.exit(0)
    if bad: sys.exit("geometry problems: not rendering")
    from multiprocessing import Pool
    jobs = int(arg("--jobs", "4")); os.makedirs("build_p2/chunks", exist_ok=True)
    edges = [round(NF * k / jobs) for k in range(jobs + 1)]
    parts = [(edges[k], edges[k + 1], f"build_p2/chunks/c{k}.mp4") for k in range(jobs)]
    with Pool(jobs) as pool: pool.map(encode_chunk, parts)
    with open("build_p2/chunks/list.txt", "w") as fh:
        for _, _, pth in parts: fh.write(f"file '{os.path.basename(pth)}'\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "build_p2/chunks/list.txt", "-c", "copy", "build_p2/video.mp4"])
    print("video ok")
    audio("build_p2/audio/mix.wav")
    run(["ffmpeg", "-y", "-i", "build_p2/video.mp4", "-i", "build_p2/audio/mix.wav", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-ar", str(SR), "-movflags", "+faststart", "-t", f"{TOTAL:.3f}", out])
    lv, tp = ebur(out)
    d = json.load(open("build/timeline_v2.json")); d.update(loudness_lufs=lv, true_peak_dbtp=tp); json.dump(d, open("build/timeline_v2.json", "w"), ensure_ascii=False, indent=1)
    print(f"{out}: {dur(out):.2f} s, {os.path.getsize(out) / 1e6:.1f} MB, {lv:.1f} LUFS, TP {tp:.1f} dBTP")
