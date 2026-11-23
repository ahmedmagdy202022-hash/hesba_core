"""M1 (HG-089): the starting visit templates that come with Hesba, in code.

Text only, on purpose. Each one is a set of prompts a doctor fills in (what to ask, what to look at) and a few lines of
general instructions to the patient. No medicine, no dose, no diagnosis: the diagnosis box is left empty and the medicine
lines are empty, so the doctor adds their own. A starting template is written once (``visit_templates.seed``), never
overwritten, and never brought back after a person edits or deletes it.

Each entry: ``(specialty, slug, {lang: (name, complaint, examination, instructions)})``. The key a row is tracked by is
``specialty.slug.lang``. A new entry added in a later release is written the next time a clinician opens the templates.
"""

SEEDS = (
    # ---- general / internal ----
    ("general", "cold", {
        "ar": ("برد وإنفلونزا", "الشكوى: رشح / عطس / كحة / وجع حلق / سخونية\nبدأت من: ... يوم\nفيه حد قريب منه عنده نفس الأعراض؟",
               "الحلق: ...\nالصدر: ...\nالأنف والودان: ...", "راحة وسوائل دافئة بكثرة.\nارجع للدكتور لو الأعراض زادت أو طولت أو ظهر عَرَض جديد."),
        "en": ("Cold and flu", "Complaint: runny nose / sneezing / cough / sore throat / fever\nStarted: ... days ago\nAnyone close with the same symptoms?",
               "Throat: ...\nChest: ...\nNose and ears: ...", "Rest and plenty of warm fluids.\nCome back if the symptoms get worse, last longer, or something new appears."),
    }),
    ("general", "sore_throat", {
        "ar": ("وجع حلق والتهاب لوز", "الشكوى: وجع حلق / صعوبة في البلع / سخونية\nبدأت من: ... يوم\nكحة أو رشح معاها؟",
               "الحلق واللوز: ...\nالغدد في الرقبة: ...\nالصدر: ...", "سوائل دافية وراحة، وبلاش المشروبات الساقعة.\nارجع للدكتور لو البلع بقى صعب أو الأعراض زادت."),
        "en": ("Sore throat and tonsillitis", "Complaint: sore throat / painful swallowing / fever\nStarted: ... days ago\nCough or runny nose with it?",
               "Throat and tonsils: ...\nNeck glands: ...\nChest: ...", "Warm fluids and rest; avoid cold drinks.\nCome back if swallowing gets harder or the symptoms get worse."),
    }),
    ("general", "gastro", {
        "ar": ("مغص وإسهال أو ترجيع", "الشكوى: مغص / إسهال / ترجيع\nبدأت من: ... ساعة أو يوم\nعدد المرات: ...\nأكل أو شرب مشكوك فيه؟ حد تاني عنده نفس الحالة؟",
               "البطن: ...\nعلامات الجفاف: ...\nالحرارة: ...", "سوائل وأملاح على قد ما يستحمل.\nأكل خفيف ويتدرّج.\nارجع فورًا لو الجفاف زاد أو فيه دم أو ألم شديد."),
        "en": ("Stomach pain, diarrhoea or vomiting", "Complaint: abdominal pain / diarrhoea / vomiting\nStarted: ... hours or days ago\nNumber of times: ...\nAny doubtful food or drink? Anyone else with the same?",
               "Abdomen: ...\nSigns of dehydration: ...\nTemperature: ...", "Fluids and salts as tolerated.\nLight food, built up slowly.\nCome back at once if dehydration gets worse, there is blood, or severe pain."),
    }),
    ("general", "hypertension_follow_up", {
        "ar": ("متابعة ضغط", "الشكوى: زيارة متابعة للضغط\nقياسات الضغط في البيت: ...\nصداع أو دوخة أو ألم في الصدر؟\nبيلتزم بالعلاج والأكل؟",
               "الضغط: ...\nالنبض: ...\nالقلب والصدر: ...\nالوزن: ...", "قياس الضغط في البيت وتسجيله.\nتقليل الملح والالتزام بالمواعيد.\nارجع قبل الميعاد لو حصل صداع شديد أو ألم في الصدر."),
        "en": ("Hypertension follow-up", "Complaint: blood pressure follow-up visit\nHome readings: ...\nHeadache, dizziness or chest pain?\nTaking the treatment and following the diet?",
               "Blood pressure: ...\nPulse: ...\nHeart and chest: ...\nWeight: ...", "Measure and record the blood pressure at home.\nLess salt and keep the appointments.\nCome back sooner for a severe headache or chest pain."),
    }),
    # ---- paediatrics ----
    ("paediatrics", "fever", {
        "ar": ("سخونية عند طفل", "الشكوى: سخونية\nبدأت من: ... يوم\nأعلى درجة حرارة اتقاست: ...\nبياكل ويشرب ويلعب عادي؟\nكحة / رشح / إسهال / ترجيع / طفح؟",
               "الحلق والودان: ...\nالصدر: ...\nالبطن: ...\nعلامات الجفاف: ...\nالنشاط: ...", "سوائل كتير وهدوم خفيفة.\nقياس الحرارة كل كام ساعة.\nارجع فورًا لو الطفل بقى خامل أو رفض الرضاعة أو الشرب أو ظهر طفح."),
        "en": ("Child with fever", "Complaint: fever\nStarted: ... days ago\nHighest temperature measured: ...\nEating, drinking and playing as usual?\nCough / runny nose / diarrhoea / vomiting / rash?",
               "Throat and ears: ...\nChest: ...\nAbdomen: ...\nSigns of dehydration: ...\nActivity: ...", "Plenty of fluids and light clothes.\nCheck the temperature every few hours.\nCome back at once if the child becomes drowsy, refuses feeds or drinks, or a rash appears."),
    }),
    ("paediatrics", "cough", {
        "ar": ("كحة وصدر عند طفل", "الشكوى: كحة / نهجان / صفير في الصدر\nبدأت من: ... يوم\nبالليل ولا بالنهار؟\nسخونية؟ تاريخ حساسية أو ربو في العيلة؟",
               "الصدر: ...\nالتنفس (عدد وجهد): ...\nالحلق: ...\nالأكسجين لو اتقاس: ...", "سوائل دافية وتهوية كويسة للأوضة، وبعيد عن الدخان.\nارجع فورًا لو التنفس بقى سريع أو متعب."),
        "en": ("Child with cough or chest symptoms", "Complaint: cough / breathlessness / wheeze\nStarted: ... days ago\nNight or day?\nFever? Allergy or asthma in the family?",
               "Chest: ...\nBreathing (rate and effort): ...\nThroat: ...\nOxygen if measured: ...", "Warm fluids, a well-aired room, and keep away from smoke.\nCome back at once if the breathing gets fast or laboured."),
    }),
    ("paediatrics", "growth_check", {
        "ar": ("متابعة نمو وتطعيمات", "الشكوى: زيارة متابعة نمو\nالرضاعة / الأكل: ...\nالنوم: ...\nالتطعيمات الأخيرة: ...\nأي قلق عند الأهل؟",
               "الوزن: ...\nالطول: ...\nمحيط الراس: ...\nالنمو الحركي والكلام: ...\nالفحص العام: ...", "التغذية حسب السن.\nمواعيد التطعيمات الجاية: ...\nالمتابعة بعد: ..."),
        "en": ("Growth and vaccination check", "Complaint: growth follow-up visit\nFeeding / diet: ...\nSleep: ...\nLatest vaccinations: ...\nAny worries from the parents?",
               "Weight: ...\nHeight: ...\nHead circumference: ...\nMotor development and speech: ...\nGeneral examination: ...", "Nutrition for the age.\nNext vaccination dates: ...\nFollow-up after: ..."),
    }),
    # ---- obstetrics and gynaecology ----
    ("obgyn", "antenatal", {
        "ar": ("متابعة حمل", "الشكوى: زيارة متابعة حمل\nأول يوم في آخر دورة: ...\nعدد الحمل / الولادات السابقة: ...\nحركة الجنين: ...\nأي نزيف أو ألم أو صداع أو ورم في الرجلين؟",
               "الضغط: ...\nالوزن: ...\nارتفاع الرحم: ...\nنبض الجنين: ...\nالسونار: ...", "الراحة والأكل الصحي وشرب مية كتير.\nمواعيد التحاليل والسونار الجاية: ...\nتروحي الطوارئ لو حصل نزيف أو ألم شديد أو الحركة قلّت."),
        "en": ("Antenatal visit", "Complaint: antenatal follow-up visit\nFirst day of last period: ...\nPregnancies / previous deliveries: ...\nFetal movement: ...\nAny bleeding, pain, headache or swollen legs?",
               "Blood pressure: ...\nWeight: ...\nUterine height: ...\nFetal heart: ...\nUltrasound: ...", "Rest, healthy food and plenty of water.\nNext tests and scans: ...\nGo to the emergency room for bleeding, severe pain or reduced movement."),
    }),
    ("obgyn", "menstrual", {
        "ar": ("اضطراب في الدورة", "الشكوى: دورة غير منتظمة / غزيرة / مؤلمة / متأخرة\nأول يوم في آخر دورة: ...\nمدة الدورة وعدد الأيام: ...\nاحتمال حمل؟ وسيلة منع حمل؟",
               "الضغط والوزن: ...\nالبطن: ...\nالفحص النسائي حسب الحالة: ...", "تسجيل مواعيد الدورة على مدار كام شهر.\nالتحاليل والسونار المطلوبة: ...\nالمتابعة بعد: ..."),
        "en": ("Menstrual irregularity", "Complaint: irregular / heavy / painful / late periods\nFirst day of last period: ...\nCycle length and days of bleeding: ...\nPossible pregnancy? Contraception?",
               "Blood pressure and weight: ...\nAbdomen: ...\nGynaecological examination as needed: ...", "Note the dates of the periods over the next few months.\nTests and scan needed: ...\nFollow-up after: ..."),
    }),
    ("obgyn", "postnatal", {
        "ar": ("متابعة بعد الولادة", "الشكوى: زيارة متابعة بعد الولادة\nتاريخ ونوع الولادة: ...\nنزيف أو إفرازات: ...\nالرضاعة: ...\nالحالة النفسية والنوم: ...",
               "الضغط: ...\nالجرح (لو قيصرية): ...\nالبطن والرحم: ...\nالثدي: ...", "راحة وتغذية كويسة وسوائل.\nوسيلة منع الحمل المناسبة: ...\nترجعي لو حصل نزيف زايد أو سخونية أو وجع في الجرح."),
        "en": ("Postnatal check", "Complaint: postnatal follow-up visit\nDate and type of delivery: ...\nBleeding or discharge: ...\nBreastfeeding: ...\nMood and sleep: ...",
               "Blood pressure: ...\nWound (if caesarean): ...\nAbdomen and uterus: ...\nBreasts: ...", "Rest, good nutrition and fluids.\nSuitable contraception: ...\nCome back for heavy bleeding, fever or wound pain."),
    }),
    # ---- ear, nose and throat ----
    ("ent", "ear_pain", {
        "ar": ("وجع في الودن", "الشكوى: وجع في الودن / إفرازات / سمع أقل / طنين\nأي ودن: يمين / شمال / الاتنين\nبدأ من: ... يوم\nسباحة أو دخول مية أو تنضيف بالقطن؟\nسخونية أو رشح؟",
               "الودن الخارجية: ...\nطبلة الودن: ...\nالحلق والأنف: ...", "الودن تفضل ناشفة وبلاش تنضيف بالقطن.\nارجع لو الوجع زاد أو خرج إفراز أو السمع قلّ."),
        "en": ("Ear pain", "Complaint: ear pain / discharge / reduced hearing / tinnitus\nWhich ear: right / left / both\nStarted: ... days ago\nSwimming, water in the ear or cleaning with cotton buds?\nFever or cold?",
               "External ear: ...\nEardrum: ...\nThroat and nose: ...", "Keep the ear dry and avoid cotton buds.\nCome back if the pain gets worse, discharge appears, or hearing drops."),
    }),
    ("ent", "sinus", {
        "ar": ("جيوب أنفية وانسداد في الأنف", "الشكوى: انسداد في الأنف / إفرازات / صداع أو ضغط في الوش / فقدان الشم\nبدأت من: ... يوم أو أسبوع\nحساسية أو عطس متكرر؟",
               "الأنف: ...\nالحلق: ...\nمنطقة الجيوب: ...\nالودان: ...", "غسيل الأنف زي ما الدكتور يوجّه وسوائل كتير.\nتجنّب مسببات الحساسية المعروفة.\nارجع لو الأعراض طولت أو ظهرت سخونية."),
        "en": ("Sinus and blocked nose", "Complaint: blocked nose / discharge / headache or facial pressure / loss of smell\nStarted: ... days or weeks ago\nAllergy or frequent sneezing?",
               "Nose: ...\nThroat: ...\nSinus area: ...\nEars: ...", "Nose rinses as the doctor directs, and plenty of fluids.\nAvoid known triggers.\nCome back if it lasts longer or a fever appears."),
    }),
    ("ent", "hoarseness", {
        "ar": ("بحة في الصوت", "الشكوى: بحة في الصوت / تعب في الصوت / كحة\nبدأت من: ... يوم أو أسبوع\nشغل بيستخدم الصوت؟ تدخين؟ ارتجاع؟",
               "الحلق: ...\nالرقبة والغدد: ...\nالأنف: ...", "راحة للصوت وسوائل دافية، وبلاش الهمس والصوت العالي.\nارجع لو البحة فضلت أكتر من أسبوعين أو ظهر صعوبة في البلع أو التنفس."),
        "en": ("Hoarse voice", "Complaint: hoarse or tired voice / cough\nStarted: ... days or weeks ago\nWork that uses the voice? Smoking? Reflux?",
               "Throat: ...\nNeck and glands: ...\nNose: ...", "Rest the voice and drink warm fluids; avoid whispering and shouting.\nCome back if hoarseness lasts over two weeks or swallowing or breathing gets difficult."),
    }),
    # ---- eye ----
    ("eye", "allergic_conjunctivitis", {
        "ar": ("حساسية في العين", "الشكوى: حكّة / احمرار / دموع / ورم خفيف في الجفون\nالعين: يمين / شمال / الاتنين\nبدأت من: ... يوم\nحساسية معروفة؟ تعرّض لتراب أو أتربة أو حيوانات؟ عدسات؟",
               "الجفون: ...\nالملتحمة: ...\nالقرنية: ...\nالنظر: يمين ... / شمال ...", "بلاش حك العين.\nكمادات باردة وغسل الوش.\nلو بتلبس عدسات: بلاش لحد ما الدكتور يسمح.\nارجع لو الأعراض زادت أو النظر قلّ."),
        "en": ("Allergic eyes", "Complaint: itching / redness / watering / mild lid swelling\nEye: right / left / both\nStarted: ... days ago\nKnown allergy? Exposure to dust or animals? Contact lenses?",
               "Lids: ...\nConjunctiva: ...\nCornea: ...\nVision: right ... / left ...", "Do not rub the eyes.\nCold compresses and washing the face.\nContact lens wearers: stop until the doctor allows.\nCome back if symptoms get worse or vision drops."),
    }),
    ("eye", "vision_check", {
        "ar": ("ضعف نظر وقياس نظر", "الشكوى: ضعف في النظر / صداع / عدم وضوح قريب أو بعيد\nبدأ من: ... \nبيلبس نظارة أو عدسات؟ آخر قياس: ...\nسكر أو ضغط أو تاريخ في العيلة؟",
               "النظر بدون نظارة: يمين ... / شمال ...\nالنظر بالنظارة: يمين ... / شمال ...\nضغط العين: ...\nقاع العين: ...", "قياس النظر وتغيير النظارة لو لزم.\nالمتابعة بعد: ...\nارجع فورًا لو حصل ضعف مفاجئ في النظر."),
        "en": ("Blurred vision and eye test", "Complaint: reduced vision / headache / blurred near or distance\nStarted: ...\nGlasses or contact lenses? Last test: ...\nDiabetes, hypertension or family history?",
               "Vision without glasses: right ... / left ...\nVision with glasses: right ... / left ...\nEye pressure: ...\nFundus: ...", "Eye test and new glasses if needed.\nFollow-up after: ...\nCome back at once for any sudden loss of vision."),
    }),
    ("eye", "eye_strain", {
        "ar": ("إجهاد وجفاف العين", "الشكوى: حرقان / جفاف / تعب في العين / صداع\nساعات الشاشة في اليوم: ...\nبدأت من: ...\nعدسات؟ تكييف أو أتربة؟",
               "الجفون: ...\nالملتحمة: ...\nالقرنية: ...\nالنظر: يمين ... / شمال ...", "راحة للعين كل فترة وبعد عن الشاشة.\nرمش متكرر وتهوية كويسة.\nارجع لو الأعراض زادت."),
        "en": ("Eye strain and dry eyes", "Complaint: burning / dryness / tired eyes / headache\nScreen hours per day: ...\nStarted: ...\nContact lenses? Air conditioning or dust?",
               "Lids: ...\nConjunctiva: ...\nCornea: ...\nVision: right ... / left ...", "Rest the eyes regularly and take breaks from screens.\nBlink often and keep the room aired.\nCome back if symptoms get worse."),
    }),
    # ---- skin ----
    ("skin", "acne", {
        "ar": ("حبوب الوش", "الشكوى: حبوب / بقع / آثار\nبدأت من: ...\nاللي اتجرّب قبل كده: ...\nدورة منتظمة؟ (للسيدات)\nمنتجات بتستخدمها على بشرتك: ...",
               "نوع الحبوب (سطحية / عميقة): ...\nمكانها: ...\nالآثار: ...\nنوع البشرة: ...", "غسيل لطيف مرتين في اليوم وبلاش عصر الحبوب.\nواقي شمس مناسب للبشرة.\nالمتابعة بعد: ..."),
        "en": ("Acne", "Complaint: spots / marks / scars\nStarted: ...\nTried before: ...\nRegular periods? (for women)\nProducts used on the skin: ...",
               "Type of lesions (superficial / deep): ...\nSite: ...\nScars: ...\nSkin type: ...", "Gentle washing twice a day; do not squeeze the spots.\nA suitable sunscreen.\nFollow-up after: ..."),
    }),
    ("skin", "rash_itching", {
        "ar": ("طفح وحكّة", "الشكوى: طفح / حكّة / احمرار\nمكانه: ...\nبدأ من: ...\nحاجة جديدة (أكل / صابون / دوا / ملابس)؟ حد قريب عنده نفس الحاجة؟",
               "شكل الطفح: ...\nالتوزيع: ...\nالجلد حواليه: ...\nالأظافر والفروة: ...", "بلاش الحك والاستحمام بمية سخنة.\nصابون خفيف ومرطّب.\nارجع لو انتشر أو ظهرت سخونية."),
        "en": ("Rash and itching", "Complaint: rash / itching / redness\nSite: ...\nStarted: ...\nAnything new (food / soap / medicine / clothes)? Anyone close with the same?",
               "Appearance of the rash: ...\nDistribution: ...\nSurrounding skin: ...\nNails and scalp: ...", "Avoid scratching and hot showers.\nA mild soap and a moisturiser.\nCome back if it spreads or a fever appears."),
    }),
    ("skin", "hair_loss", {
        "ar": ("تساقط شعر", "الشكوى: تساقط شعر / شعر خفيف / بقع\nبدأ من: ...\nمنتظم ولا بقع؟\nتغيير في الأكل أو الوزن أو ضغط نفسي أو ولادة قريب؟\nتاريخ في العيلة؟",
               "الفروة: ...\nنمط التساقط: ...\nكثافة الشعر: ...\nالأظافر: ...", "تحاليل لو لزم: ...\nعناية لطيفة بالشعر وبلاش الشد والحرارة العالية.\nالمتابعة بعد: ..."),
        "en": ("Hair loss", "Complaint: hair loss / thinning / patches\nStarted: ...\nEven or patchy?\nChange in diet, weight, stress or recent delivery?\nFamily history?",
               "Scalp: ...\nPattern of loss: ...\nHair density: ...\nNails: ...", "Tests if needed: ...\nGentle hair care; avoid pulling and high heat.\nFollow-up after: ..."),
    }),
    # ---- orthopaedics ----
    ("ortho", "back_pain", {
        "ar": ("ألم في الضهر", "الشكوى: ألم في أسفل أو أعلى الضهر\nبدأ من: ... \nبعد مجهود أو حادثة؟\nنزول الألم للرجل أو تنميل أو ضعف؟\nتحكم في البول والبراز؟",
               "الوقفة والحركة: ...\nنقطة الألم: ...\nالأعصاب (قوة وإحساس): ...\nرفع الرجل: ...", "راحة نسبية وتفادي الرفع التقيل.\nتمارين خفيفة بعد ما يهدا الألم.\nارجع فورًا لو حصل تنميل شديد أو ضعف في الرجل أو تغيّر في البول والبراز."),
        "en": ("Back pain", "Complaint: pain in the lower or upper back\nStarted: ...\nAfter effort or an accident?\nPain going down the leg, numbness or weakness?\nControl of bladder and bowel?",
               "Posture and movement: ...\nPoint of pain: ...\nNerves (power and sensation): ...\nStraight leg raise: ...", "Relative rest and no heavy lifting.\nGentle exercises once the pain settles.\nCome back at once for severe numbness, leg weakness, or a change in bladder or bowel control."),
    }),
    ("ortho", "knee_pain", {
        "ar": ("ألم في الركبة", "الشكوى: ألم / ورم / فرقعة / عدم ثبات في الركبة\nأي ركبة: يمين / شمال / الاتنين\nبدأ من: ...\nإصابة أو رياضة؟ ألم مع السلم؟",
               "الشكل والورم: ...\nنقطة الألم: ...\nمدى الحركة: ...\nثبات الأربطة: ...", "راحة وكمادات باردة في الأول.\nتفادي السلم والجلوس الطويل.\nالأشعة أو التمارين المطلوبة: ...\nالمتابعة بعد: ..."),
        "en": ("Knee pain", "Complaint: pain / swelling / clicking / instability of the knee\nWhich knee: right / left / both\nStarted: ...\nInjury or sport? Pain on stairs?",
               "Appearance and swelling: ...\nPoint of pain: ...\nRange of movement: ...\nLigament stability: ...", "Rest and cold compresses at first.\nAvoid stairs and long sitting.\nX-ray or exercises needed: ...\nFollow-up after: ..."),
    }),
    ("ortho", "sprain", {
        "ar": ("التواء أو إصابة", "الشكوى: التواء / ضربة / وقعة\nالمكان: ...\nحصلت إمتى وإزاي: ...\nقدر يمشي أو يحرّك بعدها؟\nورم أو كدمات؟",
               "الورم والكدمات: ...\nنقطة الألم: ...\nالحركة: ...\nالإحساس والنبض تحت الإصابة: ...", "راحة ورفع الطرف وكمادات باردة.\nربط أو جبيرة حسب الحالة: ...\nارجع فورًا لو الألم زاد أو لون الجلد اتغيّر أو حصل تنميل."),
        "en": ("Sprain or injury", "Complaint: sprain / blow / fall\nSite: ...\nWhen and how it happened: ...\nCould they walk or move it afterwards?\nSwelling or bruising?",
               "Swelling and bruising: ...\nPoint of pain: ...\nMovement: ...\nSensation and pulse below the injury: ...", "Rest, elevation and cold compresses.\nBandage or splint as needed: ...\nCome back at once if the pain gets worse, the skin colour changes or there is numbness."),
    }),
    # ---- dental ----
    ("dental", "toothache", {
        "ar": ("وجع ضرس", "الشكوى: وجع في ضرس / حساسية / ورم\nأي ضرس (رقم أو مكان): ...\nبدأ من: ...\nالوجع مع الساقع أو السخن أو بالليل؟\nسخونية أو ورم في الوش؟",
               "الضرس: ...\nالتسوس: ...\nالتحسس للمس والطرق: ...\nاللثة حواليه: ...\nالأشعة: ...", "تنضيف الأسنان برفق وتجنب الأكل على الناحية دي.\nارجع فورًا لو ظهر ورم في الوش أو سخونية."),
        "en": ("Toothache", "Complaint: toothache / sensitivity / swelling\nWhich tooth (number or site): ...\nStarted: ...\nPain with cold, hot or at night?\nFever or facial swelling?",
               "Tooth: ...\nDecay: ...\nTenderness to touch and tapping: ...\nGum around it: ...\nX-ray: ...", "Brush gently and avoid chewing on that side.\nCome back at once if facial swelling or fever appears."),
    }),
    ("dental", "check_up", {
        "ar": ("كشف دوري وتنضيف", "الشكوى: كشف دوري / تنضيف\nآخر زيارة: ...\nأي شكوى حالية؟\nعادات: تدخين / سكر / نشويات؟",
               "الأسنان: ...\nالجير: ...\nاللثة: ...\nالفم والخدود: ...\nالأشعة: ...", "تنضيف الأسنان مرتين في اليوم وخيط الأسنان.\nالزيارة الجاية بعد: ...\nالعلاج المطلوب: ..."),
        "en": ("Check-up and cleaning", "Complaint: routine check-up / cleaning\nLast visit: ...\nAny current complaint?\nHabits: smoking / sugar / starch?",
               "Teeth: ...\nCalculus: ...\nGums: ...\nMouth and cheeks: ...\nX-ray: ...", "Brush twice a day and use dental floss.\nNext visit after: ...\nTreatment needed: ..."),
    }),
    ("dental", "bleeding_gums", {
        "ar": ("نزيف أو التهاب لثة", "الشكوى: نزيف لثة / احمرار / ريحة فم / تحرك في الأسنان\nبدأت من: ...\nوقت النزيف: مع الفرشة / لوحده\nسكر أو أمراض مزمنة؟",
               "اللثة: ...\nالجير: ...\nعمق الجيوب: ...\nحركة الأسنان: ...", "تنضيف لطيف ومنتظم والاهتمام بما بين الأسنان.\nجلسة تنضيف: ...\nالمتابعة بعد: ..."),
        "en": ("Bleeding or inflamed gums", "Complaint: bleeding gums / redness / bad breath / loose teeth\nStarted: ...\nBleeding: when brushing / by itself\nDiabetes or chronic illness?",
               "Gums: ...\nCalculus: ...\nPocket depth: ...\nTooth mobility: ...", "Gentle regular cleaning, including between the teeth.\nCleaning session: ...\nFollow-up after: ..."),
    }),
    # ---- vet ----
    ("vet", "digestive", {
        "ar": ("ترجيع وإسهال", "الشكوى: ترجيع / إسهال / رفض الأكل\nبدأ من: ...\nعدد المرات: ...\nأكل جديد أو حاجة اتبلعت؟\nنشاطه وشربه للمية: ...",
               "الحالة العامة والنشاط: ...\nعلامات الجفاف: ...\nالبطن: ...\nالحرارة: ...", "سوائل ونظام أكل خفيف حسب توجيه الدكتور.\nمراقبة النشاط والشرب.\nارجع فورًا لو الحالة زادت أو الحيوان بقى خامل."),
        "en": ("Vomiting and diarrhoea", "Complaint: vomiting / diarrhoea / not eating\nStarted: ...\nNumber of times: ...\nNew food or something swallowed?\nActivity and drinking: ...",
               "General condition and activity: ...\nSigns of dehydration: ...\nAbdomen: ...\nTemperature: ...", "Fluids and a light diet as the vet directs.\nWatch activity and drinking.\nCome back at once if it gets worse or the animal becomes lethargic."),
    }),
    ("vet", "skin_itch", {
        "ar": ("حكّة وتساقط شعر", "الشكوى: حكّة / تساقط شعر / جروح / قشر\nمكانها: ...\nبدأت من: ...\nحيوانات تانية في البيت بنفس الحاجة؟\nآخر مضاد طفيليات وحمّام: ...",
               "الجلد والشعر: ...\nطفيليات ظاهرة: ...\nالودان: ...\nالحالة العامة: ...", "نظافة مكان الحيوان وحمّام حسب الحالة.\nمنع الحيوان من الحك أو اللحس.\nالمتابعة بعد: ..."),
        "en": ("Itching and hair loss", "Complaint: itching / hair loss / sores / scaling\nSite: ...\nStarted: ...\nOther animals at home with the same?\nLast parasite treatment and bath: ...",
               "Skin and coat: ...\nVisible parasites: ...\nEars: ...\nGeneral condition: ...", "Keep the animal's place clean; bathe as the case needs.\nStop the animal scratching or licking.\nFollow-up after: ..."),
    }),
    ("vet", "routine_check", {
        "ar": ("كشف دوري", "الشكوى: كشف دوري\nالأكل ونوعه: ...\nالنشاط والشرب: ...\nآخر تطعيم ومضاد طفيليات: ...\nأي ملاحظات من صاحب الحيوان؟",
               "الوزن: ...\nالحالة العامة: ...\nالأسنان واللثة: ...\nالجلد والشعر: ...\nالقلب والصدر: ...\nالبطن: ...", "نظام أكل مناسب للسن والوزن.\nموعد التطعيم أو المضاد الجاي: ...\nالمتابعة بعد: ..."),
        "en": ("Routine check", "Complaint: routine check-up\nFood and type: ...\nActivity and drinking: ...\nLast vaccine and parasite treatment: ...\nAny remarks from the owner?",
               "Weight: ...\nGeneral condition: ...\nTeeth and gums: ...\nSkin and coat: ...\nHeart and chest: ...\nAbdomen: ...", "A diet that suits the age and weight.\nNext vaccine or parasite treatment: ...\nFollow-up after: ..."),
    }),
)


def rows():
    """[(seed_key, specialty, lang, name, complaint, examination, instructions, position)] for every entry, in order."""

    found = []
    for index, (specialty, slug, texts) in enumerate(SEEDS):
        for lang in ("ar", "en"):
            name, complaint, examination, instructions = texts[lang]
            found.append((f"{specialty}.{slug}.{lang}", specialty, lang, name, complaint, examination, instructions, (index + 1) * 10))
    return found
