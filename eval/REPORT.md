# Aniqlik baholash hisoboti

> **Yangilanish (prognoz moduli qo'shilgandan keyin).** To'plam 76 savol bo'ldi (1 ta "rad etish" savoli va 5 ta yangi savol "prognoz" toifasiga o'tdi).
> Gemini bilan **73/76 = 96,1 %**, Gemini'siz **56/76 = 73,7 %**. Prognoz toifasi: 5/6 va 4/6 (Gemini bilan qolgan bitta xato Gemini vaqtincha javob bermagani sababli).
> Quyidagi jadvallar prognozgacha bo'lgan 71 savollik o'lchovga tegishli.
>
> **Prognoz aniqligi** (`eval/forecast_backtest.py`, oxirgi nuqtalar yashirilgan): median xato yillik 5,3 % (sodda taxmin 5,5 %), choraklik 4,0 % (7,4 %), oylik 4,4 % (10,4 %).
> Oraliq qamrovi 71 / 80 / 82 %. Oraliq kengligi shu sinov asosida sozlangan va ikkinchi tasodifiy tanlamada (seed 101: 77 / 81 / 83 %) tekshirilgan. Qatorlarning taxminan 36–39 % ida tizim prognozni rad etadi.

71 ta savol, `python eval/run_eval.py rules` va `python eval/run_eval.py gemini` bilan o'lchangan (natijalar `result_*.json` da).

## Natija
| Rejim | To'g'ri | Aniqlik | O'rtacha vaqt |
|---|---|---|---|
| Faqat qoidali dvigatel (Gemini'siz) | 53 / 71 | **74,6 %** | 0,14 s |
| Gemini + qoidali dvigatel | 69 / 71 | **97,2 %** | 4,1 s (mediana 2,9 s) |

Toifalar bo'yicha (to'g'ri / jami):
| Toifa | Gemini'siz | Gemini bilan |
|---|---|---|
| oddiy (o'zbekcha, aniq) | 10/10 | 10/10 |
| hudud | 9/10 | 9/10 |
| erkin / imlo xatoli / so'zlashuv | 4/8 | 8/8 |
| kirill | 4/4 | 4/4 |
| rus tili | 0/6 | 6/6 |
| ingliz tili | 2/6 | 5/6 |
| taqqoslash | 2/4 | 4/4 |
| dinamika | 2/2 | 2/2 |
| noaniq (profil, Toshkent) | 3/3 | 3/3 |
| rad etish (prognoz, mavzudan tashqari) | 10/10 | 10/10 |
| yangi (keyin qo'shilgan 8 ta) | 7/8 | 8/8 |

## "To'g'ri" qanday aniqlandi
Javob ma'lumotni **tizimning o'z chiqishidan emas**, bazadan olingan haqiqiy qiymat bilan tekshiriladi:
- Har savol uchun inson (men) qabul qilinadigan ko'rsatkich ID'larini katalogdan tanlagan.
- Kutilgan (hudud, davr) katakchalarning qiymati to'g'ridan-to'g'ri `observations` jadvalidan o'qiladi.
- Javob **to'g'ri** hisoblanadi, agar status `ok`, ko'rsatkich qabul qilinadiganlar ro'yxatida, va barcha kutilgan raqamlar javob faktlarida bor.
- Rad etish savollari: tizim rad etsa to'g'ri. Noaniq "Toshkent": tizim so'rasa to'g'ri.

## Gemini xatolari (2 ta qolgan)
1. "Navoiy viloyati 2023 yil eksport hajmi" → "Eksport tovarlar hajmi" (faqat tovarlar) tanlandi, hududlar kesimidagi umumiy eksport emas.
2. "GDP growth rate of Uzbekistan in 2024" → YaIM o'sish sur'atining ishlab chiqarish usuli varianti tanlandi.

Ikkalasi ham **noto'g'ri ko'rsatkich tanlash** turidagi xato (raqam bazadagi qiymatga to'g'ri, lekin boshqa, yaqin ko'rsatkichniki).

## Baholash jarayonida topilgan va tuzatilgan xatolar
- Gemini "Prezident kim?" ni "O'zbekiston Respublikasi Prezidenti kim?" deb qayta yozdi va tizim bunga raqam bilan javob berdi. Tuzatish: Gemini `on_topic` bayrog'ini qaytaradi; statistikaga tegishli bo'lmasa, qayta yozish ishlatilmaydi. Ko'rsatmaga "foydalanuvchi yozmagan narsani qo'shma" qoidasi ham kiritilgan.
- Gemini "Toshkent" ni so'ramasdan "Toshkent shahri" qildi. Tuzatish: ko'rsatmada "Toshkent"ni o'zgartirmaslik qoidasi.
- Qoidali dvigatel "O'zbekiston poytaxti qayer?" ga hudud profilini berdi (noma'lum so'z + hudud = profil qoidasi). Tuzatish: noma'lum mazmunli so'z bo'lsa, profil ko'rsatilmaydi, rad etiladi.

## Halol cheklovlar (ustozga ayting)
- **Hajmi kichik:** 71 savol. Bu ishonchli statistika emas, taxminiy ko'rsatkich. Natijani "~97 %" deb emas, "97 % atrofida (71 savolda)" deb ayting.
- **Savollarni men yozdim**, ular bitta mualliflik uslubida. Haqiqiy foydalanuvchilar boshqacha yozishi mumkin.
- **Xatolar topilgach tuzatdim**, shuning uchun 97,2 % bir oz optimistik. Shu sababli tuzatishdan keyin 8 ta yangi ("held-out") savol qo'shdim; ularning 8/8 to'g'ri, lekin bu ham kichik namuna.
- **Qabul qilinadigan ko'rsatkichlar ro'yxatini men belgilaganman:** ba'zan bir nechta ko'rsatkich ma'noviy mos bo'lishi mumkin. Masalan, milliy eksport uchun `Eksport hajmi (yillik)` (id 1142) ham qabul qilindi.
- **Tezlik:** Gemini bilan o'rtacha 4 s (bepul tarifda ba'zan 15 s gacha). 8 ta savol Gemini'ga murojaat qilmay javob olgan: ular statistikadan tashqari deb belgilangan va qoidali dvigatel rad etgan.
- Javob matni hozir faqat o'zbekcha.
