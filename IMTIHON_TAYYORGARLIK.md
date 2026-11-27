# Imtihonga tayyorgarlik: SIAT MCP loyihasi

Bu hujjat ustozga loyihani tushuntirish va savollarga javob berish uchun. Avval 1–5 bo'limni o'qing, keyin 8-bo'limdagi savol-javoblarni yodlang, oxirida demo rejasini (9) mashq qiling.

---

## 1. 30 soniyalik tanishtiruv
> Men O'zbekiston rasmiy statistikasi (siat.stat.uz) bo'yicha savollarga javob beradigan tizim yaratdim. Foydalanuvchi oddiy tilda yozadi (o'zbek lotin/kirill, rus, ingliz), tizim savolni tushunadi, **raqamni bazadan kod orqali oladi yoki hisoblaydi** va javobdagi har bir raqamni qayta tekshiradi. Til modeli faqat savolni tushunish uchun ishlatiladi, raqam hosil qilmaydi. Shuning uchun tizim raqam to'qimaydi, bazada bo'lmagan o'tgan davr so'ralsa, rad etadi; kelajak uchun faqat alohida belgilangan taxmin beradi. Arxitektura RAG emas, **MCP** (Model Context Protocol): model aniq tool'lar orqali ma'lumot oladi.

## 2. Muammo va maqsad
- siat.stat.uz da **3261 ta ko'rsatkich** (5 soha → 26 bo'lim → 66 guruh → ko'rsatkichlar), har biri hudud va davr kesimida jadval. Kerakli raqamni qo'lda topish qiyin.
- Oddiy chatbot (LLM) bunday raqamlarni **o'ylab topishi** mumkin. Rasmiy statistikada bu qabul qilinmaydi.
- Maqsad: aniq, tekshiriladigan, manbasi ko'rsatilgan javob; ma'lumot yo'q bo'lsa, "yo'q" deyish.

## 3. Nega RAG emas, MCP?
| | RAG | Bizning MCP yondashuvi |
|---|---|---|
| Ma'lumot | Matn bo'laklari (chunk) topiladi | Tuzilgan jadval (SQL) |
| Raqamni kim o'qiydi | LLM matndan "o'qiydi", adashishi mumkin | **Kod** bazadan aniq oladi |
| Hisob-kitob (foiz, farq, reyting) | LLM taxmin qiladi | Python/SQL aniq hisoblaydi |
| Tekshirish | Qiyin | Har raqam faktlar bilan solishtiriladi |
| Davr yo'q bo'lsa | Yaqin matnni "to'qib" berishi mumkin | Tool "ma'lumot yo'q" qaytaradi |

**MCP nima?** Model Context Protocol: LLM (yoki ilova) tashqi tool'larni standart usulda chaqirishi uchun protokol. Tool: nomi, kirish parametrlari va aniq natija qaytaradigan funksiya. Bizning server 10 ta tool beradi (`src/siat_mcp/server.py`); uni Claude Desktop kabi har qanday MCP mijozi ulay oladi.

## 4. Arxitektura

```
Foydalanuvchi savoli (uz/ru/en)
        │
        ▼
[Gemini]  savolni kanonik o'zbekcha savolga aylantiradi (faqat tushunish)
        │   (kalit yo'q / xato / limit → shu qadam o'tkazib yuboriladi)
        ▼
[Qoidali dvigatel — agent.py]
   normalizatsiya → hudud/davr/niyat aniqlash → ko'rsatkich qidirish
        │
        ▼
[Tool'lar — queries.py]  SQL + Python hisob  ──►  [SQLite: siat.db]
        │
        ▼
[Shablon javob]  raqamlar tool natijasidan
        │
        ▼
[Verifier]  javobdagi har raqam faktlarda bormi?  → ha: "✓ Tekshirildi", yo'q: javob rad
        │
        ▼
FastAPI  →  Veb-sahifa (web/)        yoki        MCP server → Claude Desktop
```

Fayllar: `etl.py` (yuklash), `classify.py` (kumulyativ), `normalize.py`, `search.py`, `queries.py`, `agent.py`, `llm.py` (Gemini), `server.py` (MCP), `api.py` (FastAPI), `web/` (frontend).

## 5. Texnik topshiriq bosqichlari va holati
| # | Bosqich | Nima qilindi |
|---|---|---|
| 1 | Manba tahlili | siat.stat.uz ochiq JSON API (`/api/sdmx/...`): katalog, jadval, metadata, yillar; 5 tilda nomlar; CC BY 4.0 |
| 2 | DB + ETL | To'liq katalogni yuklab SQLite'ga yozish (jadvallar: categories, indicators, members, periods, observations); qayta yuklash mumkin, 429 limitiga chidamli |
| 3 | Normalizatsiya | Lotin/kirill, apostroflar, sinonimlar (YaIM = yalpi ichki mahsulot), hududlar, davrlar (2-chorak, mart), ko'p savolni bo'lish |
| 4 | Qidiruv | 3261 ko'rsatkich orasidan IDF-vaznli so'z qidiruvi (vektor emas), suffikslarga chidamli |
| 5 | Tool'lar | qiymat, dinamika, o'sish, taqqoslash, reyting, davrlar ro'yxati va h.k. |
| 6 | LLM integratsiyasi | Gemini savolni tushunadi; xato bo'lsa, shablonli zaxira dvigatel (shartnomaga mos "fallback") |
| 7 | Anti-gallyutsinatsiya | `verify_answer`: javobdagi har bir raqam tool faktiga teng bo'lishi shart |
| 8 | API + lokal muhit | FastAPI, rate limit (40/daq), `run-web.bat`, F5 sozlamasi |
| 9 | Test + hujjat | 31 ta pytest, README, TEST_QUESTIONS.md |

Halol eslatma: Gemini bilan LLM **tool'larni o'zi chaqirmaydi**. U savolni qayta yozadi, tool'larni esa bizning dvigatel chaqiradi. Bu ongli qaror: xavfsizroq va tekshiriladigan.

## 6. Ma'lumotdagi qiyin joylar (bularni bilish "chuqur tushunganingni" ko'rsatadi)
1. **Kumulyativ qatorlar.** Choraklik/oylik oqim ko'rsatkichlari yil ichida yig'ilib boradi (2-chorak = 1+2 chorak). Agar buni bilmasak, "2-chorak" va "1-chorak" ni oddiy solishtirib, noto'g'ri farq chiqaramiz. `classify.py` buni monoton o'sish, yil chegarasida nolga qaytish va Q4/Q1 nisbati bo'yicha aniqlaydi, 14 ta holat qo'lda tuzatilgan (`cumulative_overrides.csv`). 29 ta noaniq ko'rsatkich ataylab NULL qoldirilgan, ularda davrlararo farq hisoblanmaydi.
2. **"Hududlar" aslida o'lchov a'zolari.** Ba'zi jadvallarda a'zolar hudud emas, iqtisodiy faoliyat turi (A..S).
3. **Davr yorliqlari xilma-xil:** `2023`, `2023-Q2`, `2020-M05`, `YYYY-Q5` (= butun yil), o'quv yili. Notanish yorliqlar o'tkazib yuboriladi va fayl ga yoziladi.
4. **Kirill va lotin aralashishi.** Ba'zi lotin so'zlarda kirill harflari bor (masalan lotincha "a" o'rniga kirill "а"); `normalize.py` ularni tuzatadi.
5. **Toshkent shahri va Toshkent viloyati** ikki alohida hudud: "Toshkent" deyilsa, tizim so'raydi.
6. **Rasmiy o'sish sur'ati ko'rsatkichi bor** (masalan 106,7 %). "Necha foiz o'sdi" deyilsa, biz uni qayta hisoblamay, rasmiy qiymatni o'qiymiz (o'sish = 106,7 − 100 = 6,7 %).

## 7. Anti-gallyutsinatsiya: 4 qatlam
1. **Raqamni LLM emas, kod beradi.** LLM bazani ko'rmaydi.
2. **Har natijada `source` va `facts`** (ko'rsatkich kodi, birlik, davr, qiymat).
3. **`verify_answer`:** javob matnidagi har raqam faktlardagiga teng (to'g'ri yaxlitlashga ruxsat). Mos kelmasa, javob `unverified` va rad matni.
4. **Rad etish qoidalari:** bazada bo'lmagan o'tgan davr, juda uzoq yoki ishonchsiz prognoz, mavzudan tashqari savol, topilmagan ko'rsatkich. Noaniq javobda sariq "Eslatma" chiqadi.

---

## 8. Ustoz berishi mumkin bo'lgan savollar va javoblar

**Konsepsiya**

1. **Loyiha nima qiladi?** Rasmiy statistika bo'yicha tabiiy tildagi savolga aniq, tekshirilgan, manbali javob beradi.
2. **MCP nima?** LLM va tashqi tool/ma'lumot orasidagi standart protokol. Tool nomi, parametrlari va aniq natijasi bor. Bizda 10 ta tool.
3. **Nega RAG emas?** RAG matn bo'lagini topib, LLM o'qiydi, raqamda adashishi va hisoblashda xato qilishi mumkin. Bizda ma'lumot jadval, shuning uchun SQL/Python bilan aniq olamiz va tekshiramiz (3-bo'lim jadvali).
4. **Nega vektor baza / embedding ishlatmadingiz?** Ko'rsatkich nomlari qisqa va kam (3261), IDF-vaznli so'z qidiruvi yetarli, tez (millisekundlar), tushuntirish oson va tashqi xizmat talab qilmaydi. Kamchiligi: sinonimlarni qo'lda yozish kerak. Buni Gemini qisman hal qiladi.
5. **LLM nima ish qiladi?** Faqat savolni tushunadi va standart o'zbekcha savolga aylantiradi (til aniqlash, imlo, so'zlashuv so'zlarini standart atamaga almashtirish, ko'p savolni bo'lish). Raqam yozmaydi va bazaga kirmaydi.
6. **LLM xato qilsa-chi?** Uchta himoya: (a) raqamlar baribir kodda, verifier tekshiradi; (b) "Gemini tushundi" yorlig'i ustida qanday talqin qilgani ko'rinadi; (c) noto'g'ri ko'rsatkich tanlansa, "Boshqa mos ko'rsatkichlar" ro'yxati va hisob-kitob izi bor. Eng katta xavf: noto'g'ri ko'rsatkich tanlash, buni ochiq tan olaman.
7. **Gemini ishlamasa?** Qoidali dvigatelga o'tadi, o'zbek tilidagi savollar ishlaydi. Xatodan keyin 60 soniya Gemini'ga murojaat qilinmaydi, shuning uchun sayt sekinlashmaydi.
8. **Nega Gemini?** Bepul tarif, ko'p tilni tushunadi, OpenAI-uslubidagi emas, lekin oddiy REST orqali chaqiriladi, boshqa provayderga almashtirish oson (`llm.py` bitta modul).
9. **Nega Gemini tezligi 2–3 s, oddiy dvigatel 0,1 s?** Gemini tarmoq orqali Google serveriga boradi va model token-ma'lum tartibda matn hosil qiladi. Qoidali dvigatel esa lokal SQLite va Python. Tezlikni oshirish uchun "o'ylash" o'chirilgan, temperature 0, chiqish juda qisqa, takroriy savollar keshlanadi.

**Ma'lumot va ETL**

10. **Ma'lumot qayerdan?** siat.stat.uz ochiq API, litsenziya CC BY 4.0 (manba ko'rsatilsa, erkin foydalanish).
11. **Ma'lumot qanday yangilanadi?** ETL'ni qayta ishga tushiraman; u mavjud xom fayllarni qayta ishlatadi va to'xtagan joydan davom etadi. Avtomatik jadval (cron) hozircha yo'q, bu keyingi qadam.
12. **429 (Too Many Requests) bilan qanday kurashdingiz?** So'rovlar orasida pauza, `Retry-After` hurmati, ishchilar sonini kamaytirish, va to'liq yuklanmagan fayllarni tekshirib qayta yuklash.
13. **Nega SQLite?** Bir fayl, o'rnatish talab qilmaydi, o'qish uchun juda tez, ma'lumot hajmi kichik. Ko'p foydalanuvchili yozish kerak bo'lsa, PostgreSQL'ga o'tish mumkin.
14. **Kumulyativ nima, nega muhim?** 6-bo'limning 1-bandi. Bu bo'lmasa, choraklar farqi noto'g'ri chiqadi.

**Normalizatsiya va qidiruv**

15. **Kirill va lotin qanday?** Hamma matn kanonik lotin ko'rinishiga keltiriladi (transliteratsiya, apostrof olib tashlash, aralash harflarni tuzatish), keyin solishtiriladi.
16. **"YaIM" ni qanday tushunadi?** Sinonim lug'ati (`SYNONYMS`) va Gemini.
17. **Qidiruv qanday ishlaydi?** Savoldagi so'zlar (hudud, davr kabi ortiqchalar olib tashlanib) ko'rsatkich nomlari bilan so'z o'xshashligi va IDF (noyob so'zga ko'proq vazn) bo'yicha solishtiriladi, eng yaxshi nomzodlar ball bilan tartiblanadi; davr turi (yillik/choraklik/oylik) ham ball beradi.
18. **Noaniq savol ("Toshkent")?** Tizim "shahri yoki viloyati?" deb so'raydi va tugmalar beradi.
19. **"Buxoro haqida ma'lumot ber" (ko'rsatkich yo'q)?** Hudud profili: 8 ta asosiy ko'rsatkich (aholi, YaHM, ishsizlik, ish haqi, investitsiya, eksport, sanoat, qishloq xo'jaligi), hammasi tekshirilgan.

**Xavfsizlik va sifat**

20. **SQL injection?** Hamma so'rovlar parametrlangan, foydalanuvchi matni SQL'ga qo'shilmaydi.
21. **Prompt injection?** Gemini chiqishi faqat matn (savol) bo'lib, bizning dvigatel orqali o'tadi; u buyruq bajara olmaydi, bazaga yoza olmaydi. Eng yomon holat: savol noto'g'ri talqin qilinadi, bu yorliq va izlarda ko'rinadi.
22. **API kalit qayerda?** `.env` faylida (`.gitignore` da), kodda emas. Kalit chatda ko'rinib qolgani uchun uni yangilash tavsiya qilingan.
23. **Maxfiylik?** Gemini'ga faqat foydalanuvchi savol matni ketadi; baza va natijalar ketmaydi.
24. **Suiste'molga qarshi?** `/api/ask` uchun daqiqasiga 40 so'rov chegarasi (IP bo'yicha) va savol uzunligi 600 belgi bilan cheklangan.
25. **Qanday sinadingiz?** 31 ta avtomatik test (pytest): verifier, rasmiy o'sish sur'ati, rad etishlar, Toshkent noaniqligi, kirill, ko'p savol, API, Gemini'ni soxta qiymat bilan almashtirish va zaxira. Qo'lda ~40 savol (TEST_QUESTIONS.md: oddiydan tuzoqlargacha).
26. **Aniqlik necha foiz?** 76 savollik baholash to'plamida (`eval/run_eval.py`, hisobot `eval/REPORT.md`): **Gemini bilan 96,1 % (73/76)**, Gemini'siz 73,7 % (56/76). To'g'rilik bazadagi haqiqiy qiymat bilan tekshirildi. Cheklov: namuna kichik va savollarni o'zim yozganman, shuning uchun "96 % atrofida" deyman.
27. **Baholash nimani topdi?** Gemini "Prezident kim?" ni mavzudan tashqari deb tushunmay, javob berib yuborgan edi, shuningdek "Toshkent" ni so'ramasdan "shahri" qilgan; qoidali dvigatel "O'zbekiston poytaxti qayer?" ga profil bergan. Uchalasi tuzatildi (`on_topic` bayrog'i, ko'rsatma qoidalari, profil sharti). Bu baholash nega kerakligini ko'rsatadi.
**Cheklovlar (o'zingiz aytsangiz, ishonchni oshiradi)**

28. **Nimalar ishlamaydi?** "Inflyatsiya" nomli ko'rsatkich yo'q: iste'mol narxlari indeksi beriladi va ogohlantirish chiqadi. Ayrim hududlarda aniq ko'rsatkich yo'q bo'lsa, yaqin ko'rsatkich beriladi va ogohlantirish chiqadi. Javob matni hozir faqat o'zbekcha. Prognoz va ma'lumotda yo'q davrlar rad etiladi. 29 ta ko'rsatkichda kumulyativlik noma'lum.
29. **Prognoz qanday ishlaydi va qanchalik aniq?** Kelajak davr so'ralsa, `forecast.py` bir nechta sodda modelni o'tmishda sinab (backtest), eng aniqini tanlaydi va taxmin + oraliq + o'tgan xatoni beradi. Yashirilgan ma'lumotda median xato: yillik 5,3 % (sodda taxmin 5,5 %), choraklik 4,0 % (7,4 %), oylik 4,4 % (10,4 %); oraliq 71–82 % holatni qamraydi. Yillikda model sodda taxmindan deyarli ustun emas, buni ochiq aytaman. 3 yil / 8 chorak / 24 oydan uzoqqa yoki xato katta bo'lsa, rad etadi. Prognoz rasmiy ma'lumot emas va shunday belgilanadi.
29a. **Nega neyron tarmoq (LSTM, Prophet) emas?** Qatorlar qisqa (yillikda 15 ta nuqta atrofida), murakkab model moslashib ketadi; sodda modellar tushuntirishga oson.
30. **Kelajakda nima qo'shasiz?** Javobni foydalanuvchi tilida chiqarish, baholash to'plami, avtomatik yangilash, Docker va doimiy hosting, Gemini'ga nomzod ko'rsatkichlar ro'yxatini berib tanlatish.
31. **Nega Python va FastAPI?** Rasmiy MCP SDK Python'da, SQLite o'rnatilgan, FastAPI tez va avtomatik hujjat (`/api/docs`) beradi.

**Halollik savoli: "buni o'zingiz yozdingizmi?"**
Aniq va tinch javob bering. Masalan: *"Loyihani sun'iy intellekt yordamchisi bilan birga qurdim; arxitektura qarorlarini, ma'lumot muammolarini (kumulyativ, davr yorliqlari) va testlarni tushunaman va hozir ko'rsata olaman."* Va haqiqatan ham tushunib oling: 4–7-bo'limlarni o'qing, kodda `agent.py`, `queries.py`, `llm.py`, `verify_answer` ni bir marta ko'zdan kechiring. Kerak bo'lsa, kodni o'zim qadam-baqadam tushuntirib beraman.

---

## 9. Demo rejasi (3–4 daqiqa)
1. `run-web.bat` (yoki VS Code F5), brauzerda `http://localhost:8000`. `/api/health` da `gemini+rules` ni ko'rsating.
2. **Oddiy:** "2023 yilda yalpi ichki mahsulot hajmi qancha edi?" → 1 433 975,7 mlrd so'm, yashil ✓ Tekshirildi.
3. **"Details" ochish:** ko'rsatkich kodi, birlik, tool chaqiruvlari (iz) — "raqam qayerdan" isboti.
4. **Boshqa til:** "Сколько безработных в Самарканде в 2024?" → 5,4 %, "✨ Gemini tushundi".
5. **Erkin savol:** "ajrimlar soni" yoki "qandaydir buxoroga aloqador statistika chiqarchi" → profil kartochkalari.
6. **Taqqoslash:** "Samarqand va Buxoroning 2024 yil yalpi hududiy mahsulotini solishtir".
7. **Rad etish:** "2031 yilda ishsizlik qancha bo'ladi?" va "bitcoin narxi qancha?" → raqam to'qimaydi.
8. **Noaniqlik:** "Toshkent aholi soni qancha" → tizim aniqlashtirish so'raydi.
9. **Gemini'siz:** `.env` da `SIAT_USE_LLM=0` qilib, o'zbekcha savol hamon ishlashini ayting (zaxira).

## 10. Foydali buyruqlar
```powershell
# veb-ilovani ishga tushirish
.venv\Scripts\python.exe -m siat_mcp.api --port 8000
# testlar
.venv\Scripts\python.exe -m pytest -q
# MCP server (Claude Desktop uchun)
.venv\Scripts\python.exe -m siat_mcp.server
```

## 11. Eslab qoladigan 5 ta asosiy fikr
1. **Raqamni LLM emas, kod beradi**, shuning uchun to'qimaydi.
2. **MCP, chunki ma'lumot tuzilgan** (jadval), matn emas.
3. **Har javobda manba va tekshiruv** (verifier).
4. **LLM ixtiyoriy:** faqat tushunish uchun, ishlamasa zaxira bor.
5. **Cheklovlarni bilaman va ochiq aytaman** (kumulyativ, noto'g'ri ko'rsatkich tanlash xavfi, baholash to'plami yo'qligi).
