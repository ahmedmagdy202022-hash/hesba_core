# نسخة تجريبية على الموبايل (DEMO-001)

نسخة بداتا وهمية تفتحها من أي موبايل عشان تفرّج حد على حِسبة. الداتا بترجع زي ما كانت كل ما السيرفر يعيد التشغيل (والخدمة المجانية بتنام بعد ربع ساعة من غير استخدام)، فمحدش يسجّل عليها حاجة حقيقية.

## الخطوات (مرة واحدة، حوالي 10 دقايق)
1. ادخل <https://render.com> واعمل حساب بـ GitHub.
2. **New → Web Service** → اختار الـ repo `hesba_core` → الـ branch: `develop`.
3. املا:
   - **Runtime:** Python 3
   - **Build Command:** `./build.sh`
   - **Start Command:** `./start_demo.sh`
   - **Instance Type:** Free
4. **Environment Variables** (Add Environment Variable):

   | Key | Value |
   |---|---|
   | `DEMO_MODE` | `True` |
   | `DEBUG` | `False` |
   | `SECRET_KEY` | دوس **Generate** |
   | `TRUST_PROXY_SSL_HEADER` | `True` |
   | `PYTHON_VERSION` | `3.12.7` |

5. **Create Web Service** واستنى لحد ما يكتب **Live** (أول مرة 3–5 دقايق).
6. افتح الرابط اللي فوق (زي `https://hesba-demo.onrender.com`) من الموبايل. صفحة الدخول بتقول البيانات: `owner` أو `cashier`، والباسورد `Demo-pass-1`.

> أول فتحة بعد ما الخدمة نامت بتاخد حوالي دقيقة. افتحه قبل ما تقابل صاحبك بدقيقتين.

## مهم
- **ماتحطش `DEMO_MODE` على نسخة عميل حقيقي أبداً.** النسخة الحقيقية بتتعمل بـ `render.yaml` و `docs/GO_LIVE.md`.
- لو عايز باسورد تاني للنسخة التجريبية: ضيف `DEMO_PASSWORD`.
