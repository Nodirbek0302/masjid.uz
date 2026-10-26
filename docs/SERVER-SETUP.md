# Serverga o'rnatish — qadamma-qadam (myCloud / istalgan Ubuntu VPS)

Domen: `nodir0302.duckdns.org` (DuckDNS, bepul). Hammasi kompyuterdan bitta buyruq bilan o'rnatiladi.

## A. Serverni olayotganda
- OS: **Ubuntu 22.04/24.04**, RAM 2 GB (ovozli xabarlar uchun; ovozsiz 1 GB yetadi), disk 20 GB+ (image yengil, build 1–2 daqiqa; ovoz modeli +~150 MB).
- Firewall / security group: **22 (SSH), 80, 443** ochiq. 5432 (Postgres) va 8080 ochilmaydi.
- SSH kirish: kompyuteringizdagi ochiq kalitni (`cat ~/.ssh/id_ed25519.pub`) serverga qo'shing yoki panel orqali kalit yuklang.
  Sinash: `ssh user@SERVER_IP` parolsiz kirishi kerak. Root bilan ham bo'ladi, lekin oddiy foydalanuvchi + `sudo` yaxshiroq.

## B. DuckDNS
duckdns.org → `nodir0302` qatoridagi "current ip" ga **server IP'sini** yozing → "update ip".
Tekshirish (kompyuterda): `dscacheutil -q host -a name nodir0302.duckdns.org` — server IP'sini ko'rsatishi kerak.
Sertifikat (HTTPS) domen server IP'siga qaragandan keyingina olinadi.

## C. Sirlar (kompyuterda, `.env.server`)
- `OSM_CONTACT=` — email (Overpass'ga yoziladi).
- Bot: @BotFather → `/revoke` → yangi token → `TELEGRAM_BOT_TOKEN=...` va `COMPOSE_PROFILES=osm,https,bot`.
- `ADMIN_TG_IDS=` — **o'zingizning Telegram ID**ingiz: botga `/id` yozing, javobdagi raqamni qo'ying (bir nechta bo'lsa vergul bilan).
- `POSTGRES_PASSWORD`, `ACTOR_SALT` tayyor (tasodifiy). Ularni saqlab qo'ying (parol menejeri): `.env.server` faqat shu kompyuterda.

## D. O'rnatish
```bash
cd /Users/nodirbek/ALLPROJECTS/masjid-uz
./deploy/push.sh user@SERVER_IP --run          # kodni yuboradi, .env yaratadi, bootstrap.sh ni ishga tushiradi
# xohlasangiz firewall ham:  ssh -t user@SERVER_IP 'cd masjid-uz && ENABLE_UFW=1 ./deploy/bootstrap.sh'
```
`bootstrap.sh`: Docker (yo'q bo'lsa) → swap (kichik RAM) → `.env` tekshiruvi → `docker compose up -d --build` →
sog'lomlik kutish → kunlik zaxira cron'i (03:15, 14 kun).

## E. Tekshirish
```bash
./deploy/verify.sh https://nodir0302.duckdns.org
```
Tekshiradi: health, bosh sahifa, masjidlar soni (osm-sync to'ldirgach ~2200), qidiruv, gzip, sarlavhalar, admin API yopiqligi,
DELETE yo'qligi, admin API'ning Telegram imzosiz yopiqligi, HTTPS sertifikati, HSTS. Birinchi daqiqalarda "masjid yo'q" bo'lishi mumkin: `osm-sync` birinchi yuklashni
bajarmoqda (`docker compose logs -f osm-sync`).

## F. Moderator paneli
Faqat Telegram ichida va faqat `ADMIN_TG_IDS` dagi ID uchun. Botga `/admin` yozing → "Moderator paneli" tugmasi → Mini App.
Brauzerda ochilsa "faqat Telegram orqali" xabari chiqadi. Ruxsatsiz (yoki imzo/muddati yaroqsiz) so'rovlar 401 oladi.

## G. Telegram Mini App
@BotFather → `/mybots` → bot → Bot Settings → Menu Button yoki Web App URL = `https://nodir0302.duckdns.org/`
(bot ishga tushganda menyu tugmasini `WEBAPP_URL` bo'yicha o'zi ham o'rnatadi). Botga `/start` — "Xaritani ochish" tugmasi.

## H. Kundalik
| Nima | Buyruq (serverda, `cd ~/masjid-uz`) |
|---|---|
| Holat / loglar | `docker compose ps` / `docker compose logs -f --tail=100 web` |
| Yangilash (kod) | kompyuterda `./deploy/push.sh user@IP --run` |
| Zaxira (qo'lda) | `./deploy/backup-docker.sh` |
| Tiklash | `docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean' < backups/masjid-XXXX.dump` |
| Qayta ishga tushirish | `docker compose restart web` |
| Barchasini to'xtatish | `docker compose down` (baza ma'lumoti `pgdata` volume'da saqlanadi) |

## Muammolar
- **Sertifikat olinmadi / brauzer "ulanish xavfsiz emas":** domen hali server IP'siga qaramaydi, yoki 80/443 yopiq.
  `docker compose logs caddy` ni ko'ring. DNS tarqalishi bir necha daqiqa olishi mumkin.
- **Sayt ochiladi, masjid yo'q:** `docker compose logs osm-sync`. Overpass band bo'lsa 1 soatdan keyin o'zi qayta urinadi.
- **web ko'tarilmayapti:** `docker compose logs web` (ko'pincha `.env` da POSTGRES_PASSWORD xatosi).
- **Disk to'ldi:** loglar aylantiriladi (10 MB × 3), zaxiralar 14 kundan keyin o'chadi; `docker system df`.
