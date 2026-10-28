# Kim o'zgartirdi — saqlanadigan ma'lumotlar va maxfiylik

Maqsad: suiiste'molni topish (vandalizm, takroriy xabarlar) va o'zgarishlar tarixini kim va qaysi qurilmadan qilingani bilan
birga saqlash. Ommaviy API shaxsiy ma'lumotni **ko'rsatmaydi**, faqat moderator ko'radi.

## Jadvallar
| Joy | Nima saqlanadi |
|---|---|
| `actors` (o'zgartirish/o'chirish mumkin) | `actor_id`; Telegram: `telegram_id`, username, ism, familiya, til (**faqat imzosi tekshirilgan** `initData` dan); mobil: `device_id`, platforma, OS versiyasi, ilova versiyasi, qurilma modeli; `user_agent`; `ip_hash` (/24 (IPv4) yoki /48 (IPv6) tarmog'ining tuzlangan HMAC'i, xom IP emas; 30 kundan keyin tozalanadi); hisoblagichlar; birinchi/oxirgi faollik |
| `bot_users`, `bot_subs`, `bot_sent` (Telegram bot) | faqat Telegram ID, tanlangan hudud, eslatma sozlamalari, til, obuna qilingan masjidlar va "bugun yuborildi" belgisi. Foydalanuvchi `/stop` yozsa yoki botni bloklasa hammasi o'chadi. Ovozli xabar va rasm **saqlanmaydi** (xotirada Gemini'ga yuboriladi va tashlanadi; kalit berilmasa o'qish o'chiq) |
| `mosque_history.client`, `reports.client` (o'zgarmas) | faqat **shaxsiy bo'lmagan** snapshot: kind, platform, os_version, app_version, device_model, user_agent |
| `mosque_history.actor`, `mosques.creator_id`, `reports.reporter_id` | `tg:<id>` / `device:<id>` / `client:<uuid>` — `actors` ga havola |

Telegram profili va IP bevosita o'zgarmas tarixga yozilmaydi: shaxsiy ma'lumotni so'rov bo'yicha tuzatish/o'chirish `actors`
jadvalida bajariladi, tarix butunligi buzilmaydi.

## Google (Gemini): rasm va ovozdan vaqt o'qish
Foydalanuvchi jadval rasmini yoki ovozli xabarni yuborsa, fayl o'qish uchun **Google (Gemini API, generativelanguage.googleapis.com)** ga jo'natiladi. Bot va ilovalar buni yuborishdan oldin aytadi. Server faylni diskka, bazaga va loglarga yozmaydi; qayta ishlangach xotiradan o'chadi (faqat bir soatlik xotira keshi, fayl xeshi bo'yicha). Maxfiylik matnida Google ko'rsatilishi shart. Kunlik chegara: `TIMUS_APP_MASJIDUZ_AI_DAILY_LIMIT`.

## Ommaviy API
- `creator_id` hech qachon qaytarilmaydi.
- `/masjiduz/v1/mosques/{id}/history`: `actor` faqat rol (`user`, `moderator`, `osm`), qurilma ma'lumoti yo'q.
- Moderator: `/masjiduz/v1/admin/overview` (oxirgi faollik, kim va qaysi qurilma) va `/masjiduz/v1/admin/actors?q=` (qidiruv).

## Mobil ilova sarlavhalari (har so'rovda)
`X-Device-Id` (o'rnatishga xos barqaror id, 8–64 belgi), `X-Client-Platform` (`ios`/`android`), `X-App-Version`,
`X-OS-Version`, `X-Device-Model`. Telegram Mini App: `X-Telegram-Init-Data`. Veb: `X-Client-Id` (avtomatik).
Ishonch tartibi: tasdiqlangan Telegram → mobil `X-Device-Id` → veb `X-Client-Id`.

## Foydalanuvchi so'rovi bo'yicha o'chirish (qo'lda)
```sql
UPDATE actors SET tg_username=NULL, tg_first_name=NULL, tg_last_name=NULL, tg_language=NULL, telegram_id=NULL,
                  device_id=NULL, device_model=NULL, user_agent=NULL, ip_hash=NULL WHERE actor_id = 'tg:123456';
```
Tarix yozuvlari qoladi (faqat opaque id bilan). Ilovada foydalanuvchiga nima saqlanishini aytib qo'ying (maxfiylik matni).

## Sozlama
`ACTOR_SALT` — IP xeshi uchun tuz (bo'sh bo'lsa `ADMIN_TOKEN` dan olinadi). O'zgartirsangiz eski xeshlar mos kelmay qoladi.

## Moderator kirishi
Moderator = `ADMIN_TG_IDS` dagi Telegram ID, imzosi tekshirilgan Mini App ma'lumoti bilan (`admin_session` 2 soat). Moderator amallari
tarixda `admin:<telegram id>` bo'lib yoziladi; ommaviy API'da faqat "moderator" roli ko'rinadi.
