# Retail promo video: how it was built

The first version (27 Nov 2026) was rejected by the owner on 28 Nov: the script was not premium and spoke only to small shops. Everything here builds the current version. In the work folder the output is named `hesba-retail-promo-v2.mp4`; it is committed one level up as `hesba-retail-promo.mp4`.

Paths are relative to a scratch work folder holding these scripts, a read-only checkout of the product (`../retail-src`), and the voice clips (`vo3/lines/p*.wav`, converted from `../voice/p*.mp3`). `env.sh` (local settings for the throwaway demo server) is deliberately not committed; see `env.example`. `timeline.json` here is the rendered timeline (`build/timeline_v2.json` in the work folder).

## Current version (28 Nov 2026): premium script for owners of any size
`hesba-retail-promo-v2.mp4` (QA revision 2): 57.71 s, 14.3 MB, 1920x1080, 30 fps, H.264 + AAC, `+faststart`. Loudness on the mp4 is −16.1 LUFS integrated, true peak −2.3 dBTP. The pre-QA config is backed up in `build_p2/*.r1.*`.
The narration track alone, levelled and on the video's timeline, is `out/hesba-retail-promo-v2-voice.mp3`. The voice clips are the ready ones in `vo3/lines/p*.wav`; no audio was generated.
The v1 files and scripts are untouched. They were already named `*_v2` (inherited from `../video-promo`), so the v2 scripts use **`_p2`** names: `capture_p2.js`, `prep_p2.py`, `frames_p2.js`, `render_p2.py`, `check_p2.py`, `p2.json`, `script_p2.json`, and `prep_data_v2.js` for the demo data. Outputs go to `shots_p2/` and `build_p2/`, plus `build/timeline_v2.json` and `check/*_v2*`.

## Rebuild (v2)
```
./build.sh                                   # only if env/ is gone (then also: source env.sh; cd ../retail-src; python manage.py prepare_demo_templates --force --only commercial:grocery)
(setsid ./serve.sh &)                        # port 8099
export NODE_PATH=$(npm root -g)
node mkbox.js commercial grocery && mv explore/state-grocery.json explore/state-p2.json   # fresh v2 sandbox (keep the v1 state file elsewhere first)
node prep_data_v2.js entities && node prep_data_v2.js locations && node prep_data_v2.js cashboxes
node prep_data_v2.js rename && node prep_data_v2.js transfers && node prep_data_v2.js transfers2
node prep_data_v2.js testsale && node prep_data_v2.js sales  # sales through the till, per branch tab
node capture_p2.js                           # every screen -> shots_p2/ (+ named target boxes); fails on visible demo chrome
python3 prep_p2.py                           # 2x on-video size -> build_p2/img; resolves outline rects -> build_p2/p2_resolved.json
node frames_p2.js                            # layers -> build_p2/layers (checks fonts, caption text = script, no overflow)
python3 render_p2.py x --tl                  # timeline + cue table + geometry checks (phone never cropped, outlines inside, band opaque)
python3 render_p2.py hesba-retail-promo-v2.mp4 --jobs 4     # about 12 min; writes build/timeline_v2.json and out/hesba-retail-promo-v2-voice.mp3
python3 check_p2.py                          # format, faststart, loudness, captions, caption time; check/contact_v2.png, check/contact_v2_3s.png, check/full_v2/
pkill -f "gunicorn config.wsgi --bind 127.0.0.1:8099"
```

## How v2 differs from v1
- **Segments.** A line can change screens. `p2.json` → `lines.<id>.segs` lists, per line, what is on the laptop and phone or which card shows, with `at` = the second in the clip when the new screen is fully in.
- **Cues.** Outline and push times are seconds in the clip, measured with silencedetect (−30 dB, 35 ms) and 30 ms energy. The `words` field records the word onsets used.
- **No doubled text.** Every change is a dip.
  - **Between lines:** the old frame dissolves into the new scene's bare frame (device bodies only) and then the new content comes in, 0.4 s + 0.4 s. After a zoomed scene the bare frame is the plain background.
  - **Screen changes:** these dip to the app's blank page. They take 0.8 s, except the p2 quick stills (0.5 s) and the p6 report-to-daily change (0.6 s).
- **No phone crop.** In a push-in the phone fades out (0.6 s) before it would touch the frame edge. `render_p2.py` checks every frame.
- **Captions.** The band, font and gold rule are the same as v1. Every caption is fully on for at least 4.0 s, and the lines are 0.6 s lead + clip + 0.7 s.
- **Data.** The demo data was made through the UI by `prep_data_v2.js`. It added two branches with their warehouses and cashboxes, stock transfers, and sales per branch tab. The main entity was renamed «الفرع الرئيسي» (brief step 6). Findings are in the session report.

## Timeline (v2)
See `check/timeline_v2.md` (scene, clip, start, end, voice, caption fully on, every cue) and `build/timeline_v2.json`.

## v2 QA revision (28 Nov)
- **Demo data, through the UI (`prep_data_v2.js fixnames`, `company`).** The cashboxes «البنك الأهلي — الحساب الجاري» and «ماكينة الفيزا» are renamed to «حساب البنك — الجاري» and «ماكينة الدفع بالكارت». The company phone, address and tax number are filled with obvious demo values.
- **Capture (`capture_p2.js`).**
  - The backup reminder is hidden with CSS, like the demo bar.
  - New shots: `dash_hero_desk` (p1, slow push toward «مبيعات اليوم») and `stock_phone` (p5b).
  - The new-branch form is typed with BR4 / «فرع الإسكندرية» / «فرع (نفس الشركة)» and is never submitted.
  - Phone scrolls are nudged so that no line sits across the sticky header or the bottom bar.
- **Captions.** The p2 band caption is «فروع ومخازن وفريق شغل». The question «مين شايف الصورة كلها؟» is on the navy card only: it starts at «بس مين شايف», is fully readable for 3.5 s, and holds 2.3 s after the clip.
- **Transitions.**
  - Screen changes inside a line are direct 0.2 s crossfades, with no blank page.
  - Between two device scenes, the screens crossfade (0.24 s, mid-transition) while the band captions go out and then in, so they are never doubled.
  - Changes to or from cards, and changes out of a zoomed scene, dip through navy.
  - There is no fade-in at the start: frame 0 is the first scene.
- **Timing.** p5b's reorder page and p6's «الخزن» are fully in (with their outlines) when «واقتراح» and «والخزنة» start. The p5a push stops at 1.42x.
