#!/usr/bin/env python3
"""Checks on the v2 video: python3 check_p2.py [hesba-retail-promo-v2.mp4]
-> check/report_v2.txt, check/timeline_v2.md, check/contact_v2.png (one frame per scene, at its last outline or its middle, with its caption),
   check/contact_v2_3s.png (a frame every 3 s), check/full_v2/*.png (full-size frames: every outline once fully on, every push midpoint, every screen-dip midpoint)."""
import json, os, re, subprocess, sys
from PIL import Image, ImageDraw
V_ = sys.argv[1] if len(sys.argv) > 1 else "hesba-retail-promo-v2.mp4"
os.chdir(os.path.dirname(os.path.abspath(__file__)))
TL = json.load(open("build/timeline_v2.json")); C = json.load(open("build_p2/p2_resolved.json"))
script = json.load(open("script_p2.json", encoding="utf-8")); brief = json.load(open("captions_brief_p2.json", encoding="utf-8"))
META = json.load(open("build_p2/layers/meta.json", encoding="utf-8"))
out = []; bad = 0


def say(s): out.append(s); print(s)


pr = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", V_]))
vs = [s for s in pr["streams"] if s["codec_type"] == "video"][0]; a_ = [s for s in pr["streams"] if s["codec_type"] == "audio"][0]
say(f"video: {vs['codec_name']} {vs['profile']} {vs['width']}x{vs['height']} {vs['r_frame_rate']} fps {vs['pix_fmt']}; audio: {a_['codec_name']} {a_['sample_rate']} Hz {a_['channels']} ch")
D = float(pr["format"]["duration"]); say(f"duration {D:.2f} s, size {int(pr['format']['size']) / 1e6:.1f} MB")
ok = vs["codec_name"] == "h264" and a_["codec_name"] == "aac" and vs["width"] == 1920 and vs["height"] == 1080 and vs["r_frame_rate"] == "30/1"
data = open(V_, "rb").read(1 << 20); atoms = []; i = 0
while i < len(data) - 8:
    n = int.from_bytes(data[i:i + 4], "big"); atoms.append(data[i + 4:i + 8].decode("latin1"))
    if n < 8: break
    i += n
fast = "moov" in atoms and ("mdat" not in atoms or atoms.index("moov") < atoms.index("mdat"))
say(f"atoms {atoms[:4]} -> faststart {'yes' if fast else 'NO'}; format {'OK' if ok else 'WRONG'}"); bad += (not ok) + (not fast)
o = subprocess.run(["ffmpeg", "-nostats", "-i", V_, "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr; o = o[o.rindex("Summary:"):]
I = float(re.search(r"I:\s+(-?[\d.]+) LUFS", o).group(1)); TP = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", o).group(1))
say(f"loudness on the mp4: {I:.1f} LUFS integrated, true peak {TP:.1f} dBTP -> {'OK' if abs(I + 16) <= 0.5 and TP <= -1.5 else 'OUT OF SPEC'}"); bad += not (abs(I + 16) <= 0.5 and TP <= -1.5)
if os.path.exists("out/hesba-retail-promo-v2-voice.mp3"): say(f"narration track: out/hesba-retail-promo-v2-voice.mp3, {float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', 'out/hesba-retail-promo-v2-voice.mp3'])):.2f} s")
for s in script:
    lid = s["id"]
    got = META["cards"]["p8"]["head_text"] if lid == "p8" else META["cards"]["p9"]["head_text"] if lid == "p9" else META["lines"][lid]["caption_text"]
    same = brief[lid] == s["caption"] == got; bad += not same
    extra = ""
    if lid == "p2":
        cs = brief["p2_card"] == s["card"] == META["cards"]["q"]["head_text"]; bad += not cs
        extra = f"  + card «{s['card']}» {'identical' if cs else 'DIFFERS'}"
    say(f"caption {lid:<4} {'identical' if same else 'DIFFERS'} (brief / script_p2.json / rendered innerText){extra}")
say(f"URL pill {META['cards']['p9']['pill_text']!r}")
say("caption fully on (scene start + 0.8 s to scene end - 0.8 s):")
for e in TL["scenes"]:
    a, b = e["caption_full"]
    if e["id"] == "p2":
        q = [q for q in e["cues"] if q["what"] == "screen -> q"][0]; card_full = q["dip"][1]
        say(f"  p2   band «فروع ومخازن وفريق شغل» {q['dip'][0] + 0.2 - a:.2f} s fully on (until the card dip; the clip reaches «بس مين شايف» there)")
        say(f"  p2   card «مين شايف الصورة كلها؟» fully on {card_full:.2f}-{b:.2f} = {b - card_full:.2f} s; voice ends {e['voice_end']:.2f}, card holds {b - e['voice_end']:.2f} s after it -> {'OK' if b - card_full >= 3.5 and b - e['voice_end'] >= 1.5 else 'SHORT'}")
        bad += not (b - card_full >= 3.5 and b - e["voice_end"] >= 1.5); continue
    say(f"  {e['id']:<4} {b - a:.2f} s {'OK' if b - a >= 4.0 else 'UNDER 4 s'}"); bad += (b - a) < 4.0
dips = [(e["id"], q["what"], q["dip"][1] - q["dip"][0]) for e in TL["scenes"] for q in e["cues"] if "dip" in q]
say("transitions: line changes dip 0.4 + 0.4 s; screen dips: " + ", ".join(f"{i} {w.split('-> ')[1]} {d:.1f} s" for i, w, d in dips) + f"; push-ins {C['push_dur']} s; outline fades {C['hl_fade']} s; phone fade {C['phone_fade']} s")
rows = ["| scene | clip | start | end | voice | caption fully on | cues (screen changes / push / outlines: on → full) |", "|---|---|---|---|---|---|---|"]
for e in TL["scenes"]:
    cs = []
    for q in e["cues"]:
        if "dip" in q: cs.append(f"{q['what']} {q['dip'][0]:.2f}–{q['dip'][1]:.2f}")
        elif "t" in q: cs.append(f"{q['what']} {q['t'][0]:.2f}–{q['t'][1]:.2f}")
        else: cs.append(f"{q['what']} {q['on']:.2f}→{q['full']:.2f}" + (f" (off {q['off']:.2f})" if q.get("off") else ""))
    rows.append(f"| {e['id']} | {os.path.basename(e['voice_file'])} | {e['start']:.2f} | {e['end']:.2f} | {e['voice_start']:.2f}–{e['voice_end']:.2f} | {e['caption_full'][0]:.2f}–{e['caption_full'][1]:.2f} | {'; '.join(cs) or '—'} |")
open("check/timeline_v2.md", "w", encoding="utf-8").write(f"Total {TL['total']:.2f} s\n\n" + "\n".join(rows) + "\n"); say("\n".join(rows))


def grab(t, path, scale=None):
    vf = ["-vf", f"scale={scale}:flags=lanczos"] if scale else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", V_, "-frames:v", "1", *vf, path], check=True)


os.makedirs("check/full_v2", exist_ok=True); tiles = []
for e in TL["scenes"]:
    ts = [(f"{e['id']}_hl{k}", q["full"] + 0.15) for k, q in enumerate([q for q in e["cues"] if "full" in q])]
    ts += [(f"{e['id']}_push", (q["t"][0] + q["t"][1]) / 2) for q in e["cues"] if q["what"] == "push-in"]
    ts += [(f"{e['id']}_dip{k}", (q["dip"][0] + q["dip"][1]) / 2) for k, q in enumerate([q for q in e["cues"] if "dip" in q])]
    if not ts: ts = [(f"{e['id']}_mid", (e["caption_full"][0] + e["caption_full"][1]) / 2)]
    for name, t in ts: grab(t, f"check/full_v2/{name}_{t:05.2f}.png")
    full = [q["full"] for q in e["cues"] if "full" in q]
    tc = min(max(full) + 0.3, e["caption_full"][1] - 0.1) if full else (e["caption_full"][0] + e["caption_full"][1]) / 2
    grab(tc, "/tmp/_c.png" if False else f"check/full_v2/_contact_{e['id']}.png", "640:360")
    im = Image.open(f"check/full_v2/_contact_{e['id']}.png").convert("RGB"); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 150, 22], fill=(0, 0, 0)); d.text((5, 5), f"{e['id']}  {tc:.2f}s", fill=(255, 255, 255)); tiles.append(im)
cols = 4; rows_n = (len(tiles) + cols - 1) // cols; S = Image.new("RGB", (cols * 646 + 6, rows_n * 366 + 6), (255, 255, 255))
for k, im in enumerate(tiles): S.paste(im, (6 + (k % cols) * 646, 6 + (k // cols) * 366))
S.save("check/contact_v2.png")
subprocess.run(["python3", "contact.py", V_, "check/contact_v2_3s.png"], check=True)
open("check/report_v2.txt", "w", encoding="utf-8").write("\n".join(out) + "\n")
print("problems:", bad); sys.exit(1 if bad else 0)
