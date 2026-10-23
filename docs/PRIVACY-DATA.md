# Kim o'zgartirdi — saqlanadigan ma'lumotlar va maxfiylik

Maqsad: suiiste'molni topish (vandalizm, takroriy xabarlar) va o'zgarishlar tarixini kim va qaysi qurilmadan qilingani bilan
birga saqlash. Ommaviy API shaxsiy ma'lumotni **ko'rsatmaydi**, faqat moderator ko'radi.

## Jadvallar
| Joy | Nima saqlanadi |
|---|---|
| `actors` (o'zgartirish/o'chirish mumkin) | `actor_id`; Telegram: `telegram_id`, username, ism, familiya, til (**faqat imzosi tekshirilgan** `initData` dan); mobil: `device_id`, platforma, OS versiyasi, ilova versiyasi, qurilma modeli; `user_agent`; `ip_hash` (tuzlangan HMAC, xom IP emas); hisoblagichlar; birinchi/oxirgi faollik |
| `mosque_history.client`, `reports.client` (o'zgarmas) | faqat **shaxsiy bo'lmagan** snapshot: kind, platform, os_version, app_version, device_model, user_agent |
| `mosque_history.actor`, `mosques.creator_id`, `reports.reporter_id` | `tg:<id>` / `device:<id>` / `client:<uuid>` — `actors` ga havola |

Telegram profili va IP bevosita o'zgarmas tarixga yozilmaydi: shaxsiy ma'lumotni so'rov bo'yicha tuzatish/o'chirish `actors`
jadvalida bajariladi, tarix butunligi buzilmaydi.

## Ommaviy API
- `creator_id` hech qachon qaytarilmaydi.
- `/api/mosques/{id}/history`: `actor` faqat rol (`user`, `moderator`, `osm`), qurilma ma'lumoti yo'q.
- Moderator: `/api/admin/overview` (oxirgi faollik, kim va qaysi qurilma) va `/api/admin/actors?q=` (qidiruv).

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
