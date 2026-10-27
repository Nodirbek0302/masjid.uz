# Docker, CI/CD va serverga chiqarish (ai-smm-agent bilan bir xil sxema)

```
git push main ──► GitHub Actions
                   ├─ CI  (ci.yml):     gitleaks → ruff → testlar (PostgreSQL) → image build + compose smoke test
                   └─ CD  (docker.yml): image build → Docker Hub (latest, sha-xxxxxxx, vX.Y.Z)
                                         └─ DEPLOY_ENABLED=true bo'lsa: SSH → docker compose pull/up → /api/health
Server: docker compose (postgres + web + bot + cloudflared), hech narsa build qilinmaydi.
```

## 0. Eng oddiy yo'l: kichik server, compose hammasini o'zi ko'taradi (hozirgi bosqich)
Docker Hub va CI/CD keyinroq kerak bo'lsa ham, server hozirning o'zida ishlaydi — image serverning o'zida quriladi
(bu image yengil: Python + psycopg, 1–2 daqiqa).
```bash
# 1) loyihani serverga yuboring (git clone yoki):  rsync -a --exclude .venv --exclude .git --exclude .pgdata ./ user@server:~/masjid-uz/
# 2) serverda:
cd ~/masjid-uz && cp .env.example .env && nano .env      # POSTGRES_PASSWORD, ADMIN_TG_IDS, DOMAIN, COMPOSE_PROFILES ...
./deploy/bootstrap.sh
```
Kompyuteringizdan bir buyruq bilan: `./deploy/push.sh user@SERVER_IP --run` — kodni yuboradi, serverda `.env` yo'q bo'lsa
`.env.server` shablonidan yaratadi (bor bo'lsa tegmaydi) va `bootstrap.sh` ni ishga tushiradi.

`bootstrap.sh` Docker'ni o'rnatadi (yo'q bo'lsa), 1GB serverda swap yaratadi, `docker compose up -d --build` qiladi va
`/api/health` ni kutadi. Server qayta yonsa ham konteynerlar o'zi qaytadi (`restart: unless-stopped`). Yangilash:
`git pull && docker compose up -d --build`.

**HTTPS:** `COMPOSE_PROFILES=https,bot` + `DOMAIN=...` (A yozuvi server IP'siga) — Caddy sertifikatni o'zi oladi.
Cloudflare Tunnel xohlasangiz `cloudflare` profilini tanlang. `bot` profili uchun `TELEGRAM_BOT_TOKEN` va `WEBAPP_URL` kerak.

### Keyin Docker Hub'ga o'tish
`docker-compose.yml` dagi `image:` nomi allaqachon Docker Hub nomiga teng. CD (`docker.yml`) image'ni yuborgach serverda
`make prod-pull prod-up` ishlatiladi (quyida), serverda build qilinmaydi. O'tish uchun compose faylini o'zgartirish shart emas.

## 1. GitHub (bir marta) — hozircha SHART EMAS
> AWS/EC2 kerak emas. `SERVER_HOST`, `SERVER_USER`, `SERVER_SSH_KEY` — istalgan VPS'ga SSH bilan chiqarish uchun GitHub
> secret nomlari (`SERVER_HOST` = server IP yoki domen). §0 yo'lida (serverda `bootstrap.sh`) ular umuman ishlatilmaydi.
> Deploy job'i `DEPLOY_ENABLED=true` qo'yilmaguncha o'chiq turadi; `DOCKERHUB_*` image'ni Docker Hub'ga yuborishda kerak.
> Ya'ni: server tayyor bo'lsa — §0 yetarli. CI/CD'ni keyin, kerak bo'lganda yoqasiz.

Image nomi: `<DOCKERHUB_USERNAME>/masjid-uz`. `docker-compose.prod.yml` dagi standart `no0404ir0302/masjid-uz` — akkauntingiz boshqa bo'lsa `IMAGE=` bering.

## 2. Server (bir marta)
```bash
curl -fsSL https://get.docker.com | sh            # Docker + compose plugin (compose >= 2.24)
mkdir -p ~/masjid-uz && cd ~/masjid-uz
# serverga faqat 3 ta fayl kerak (kod kerak emas, image tayyor):
#   docker-compose.yml, docker-compose.prod.yml, .env        (scp bilan yuboring)
```
`.env` (server, `chmod 600`) — namuna `.env.example` da. Kamida:
```
POSTGRES_PASSWORD=<openssl rand -hex 24>          # faqat hex: URL ichida maxsus belgi muammo bo'ladi
ADMIN_TG_IDS=<sizning Telegram ID, botga /id yozing>
TELEGRAM_BOT_TOKEN=<@BotFather'dan YANGI token>
WEBAPP_URL=https://<domeningiz>/
CLOUDFLARE_TUNNEL_TOKEN=<Zero Trust → Networks → Tunnels>   # public hostname → http://web:8080
```
Birinchi ishga tushirish: `make prod-pull prod-up` (yoki CD birinchi push'da o'zi qiladi).
Migratsiyalar `web` ishga tushganda avtomatik qo'llanadi.

## 3. Kundalik
- Kod o'zgardi → `git push` → CI yashil → CD image yuboradi → server yangilanadi, `/api/health` tekshiriladi.
- Rollback: `IMAGE=<user>/masjid-uz:sha-abc1234 make prod-up`.
- Loglar: `make prod-logs`. Holat: `make prod-ps`.

## 4. Moderator paneli (faqat Telegram, faqat sizning ID)
Panel `https://<domen>/admin.html` da turadi, lekin **faqat Telegram Mini App ichida** va faqat `.env` dagi `ADMIN_TG_IDS`
ro'yxatidagi Telegram ID uchun ochiladi: server Telegram imzosini (bot tokeni bilan HMAC) tekshiradi, 2 soatdan eski
ma'lumotni rad etadi. Brauzerda ochilsa, yoki ID ro'yxatda bo'lmasa — ruxsat yo'q. ID bo'sh bo'lsa hech kim kira olmaydi.
Ochish: botga `/admin` yozing (faqat ro'yxatdagi ID'larga "Moderator paneli" tugmasi chiqadi). ID'ni bilish: botga `/id`.
Token bilan kirish faqat dasturlash uchun (`ADMIN_ALLOW_TOKEN=1`), serverda yoqilmaydi.

## 5. OSM masjidlari
`COMPOSE_PROFILES` da `osm` bo'lsa hech narsa qilish shart emas: birinchi ishga tushishda baza to'ladi, keyin har 24 soatda yangilanadi (`docker compose logs osm-sync`). Qo'lda: docs/IMPORT-OSM.md.

## 6. Zaxira
`make backup` (yoki cron: `15 3 * * * cd ~/masjid-uz && make backup`). Tiklash:
`docker compose exec -T postgres pg_restore -U masjid -d masjid --clean < backups/masjid-YYYY-MM-DD.dump`. Dump'ni serverdan tashqariga ham nusxalang.

## Eslatmalar
- `docker-compose.yml` dagi `ADMIN_LOCAL_ONLY`, `TRUST_PROXY`, `DATABASE_URL` compose ichida belgilanadi va `.env` dagi qiymatlardan ustun turadi.
- Image va compose **bu kompyuterda (Docker yo'q) ishga tushirib ko'rilmagan**: birinchi tekshiruv CI (`docker` job) bo'ladi.
- Telegram Mini App `WEBAPP_URL` HTTPS bo'lishini talab qiladi: Cloudflare Tunnel buni beradi.

## Telegram: lokatsiya + jadval rasmi orqali vaqtlarni yangilash
Foydalanuvchi botga masjid **lokatsiyasini** (📎 → Joylashuv) yuboradi → 200 m ichidagi masjidlardan birini tanlaydi (yo'q bo'lsa yangi
qo'shadi) → **namoz jadvali rasmini** yuboradi (yoki 5 ta vaqtni matn bilan yozadi) → bot topgan vaqtlarni ko'rsatadi → "Ha, saqlash" →
vaqtlar saqlanadi va bot "shu masjid uchun vaqtlar o'zgardi, tekshirib ko'rishingiz mumkin" deb Mini App'da masjidni ochuvchi tugma beradi
(`/?m=<id>`). Rasm o'qish serverning o'zida **Tesseract** bilan bajariladi (Docker image'da o'rnatilgan, `rus`, `eng`, `uzb`, `uzb_cyrl`):
rasm uchinchi tomonga yuborilmaydi va saqlanmaydi. Ishonchsiz o'qilgan vaqt hech qachon tasdiqsiz saqlanmaydi; ikki ustunli jadvalda
foydalanuvchi jamoat ustunini tanlaydi; soatiga 30 ta rasm chegarasi bor. `bot` profili `DATABASE_URL` ni oladi (compose'da sozlangan).

## Telegram: ovozli xabar orqali vaqtlarni yangilash
Rasm o'rniga foydalanuvchi **ovozli xabar** yuborishi mumkin: «Bomdod besh yigirma, Peshin o'n ikki o'ttiz, Asr to'rt, Shom besh o'ttiz besh,
Xufton yetti». O'zbek (lotin/kirill), rus va ingliz tilida tushuniladi; **AM/PM aytish shart emas**: 12 soatlik raqamlar har namozning o'z
vaqt oralig'i va qo'shni namozlar orasidagi tabiiy farqqa qarab joylashtiriladi (Asr «6:00» — Shom 17:35 bo'lsa 16:00, Shom 19:45 bo'lsa 18:00).
Natija har doim tasdiqlash uchun ko'rsatiladi («Eshitilgan: …» + 5 ta vaqt); tasdiqlanmaguncha hech narsa saqlanmaydi.
Nutqni matnga o'girish serverning o'zida **faster-whisper** (`WHISPER_MODEL`, standart `base`, ~140 MB) bilan bajariladi: audio uchinchi tomonga
yuborilmaydi va saqlanmaydi. Image'da `WITH_VOICE=1` (standart); `docker build --build-arg WITH_VOICE=0 .` bilan ovozsiz, ~250 MB yengilroq image
olinadi (bot rasm/matn so'raydi). Model birinchi ishga tushishda yuklanadi va `whisper` volume'da saqlanadi. Bot konteyneri uchun **kamida 2 GB RAM**
tavsiya etiladi (limit 768M). O'zbek tilida aniqlik rus/ingliz tilidagidan pastroq bo'lishi mumkin: shuning uchun tasdiqlash qadami majburiy.

## Telegram: eng yaqin masjid, obuna va eslatmalar
- **Eng yaqin masjid:** botga lokatsiya yuborilganda (yoki `/nearest`) karta chiqadi: nomi, masofa, manzil, jamoat vaqtlari, oxirgi yangilanish,
  «🧭 Yo'nalish», «✏️ Vaqtlarni yangilash», «🔔 Obuna bo'lish».
- **Masjidga obuna** (`/subs`, kartadagi tugma, `https://t.me/<bot>?start=s_<masjid id>` havolasi — veb sahifada ham «🔔 Obuna (Telegram)»):
  masjid jamoat vaqtlari o'zgarsa bot xabar beradi (vaqtni o'zgartirgan odamning o'ziga yuborilmaydi). Bir kishi ko'pi bilan 20 masjidga obuna bo'ladi.
- **Eslatmalar** (`/reminders`): hudud (14 ta) tanlanadi; «har kungi xabar» (Bomdoddan oldin, 5 vaqt + quyosh) va «namozdan 15 daqiqa oldin».
  Vaqtlar astronomik hisob bo'yicha (veb sahifadagi bilan bir xil), O'zbekiston vaqti (UTC+5). Har xabar kuniga **bir marta** yuboriladi
  (`bot_sent` jadvali: bot qayta ishga tushsa ham takrorlanmaydi). Bot bloklangan foydalanuvchining ma'lumotlarini o'zi o'chiradi.
- `/stop` — foydalanuvchining barcha bot ma'lumotlari (obuna, eslatma sozlamalari) o'chadi.

## Til (bot va veb bir xil tilda)
Botga birinchi `/start` yozganda til so'raladi (Oʻzbekcha, Ўзбекча, Русский, English). Telegramning o'z tili **ishlatilmaydi**.
Tanlov `bot_users.lang` da saqlanadi (`lang_set=true`), `/lang` bilan istalgan payt o'zgartiriladi. Shu tildan:
- botning hamma xabari, buyruqlar menyusi (chat uchun) va eslatmalar;
- Mini App havolalari (`?lang=...`) va chatning menyu tugmasi (`setChatMenuButton`, har chat uchun alohida);
- Mini App ochilganda `GET /api/me` (Telegram imzosi tekshiriladi) bot tanlagan tilni qaytaradi; veb'da til almashtirilsa
  `POST /api/me/lang` botni ham shu tilga o'tkazadi. Kirill matnlar lotindan avtomatik hosil qilinadi (`server/translit.py`).
Tilni tanlamagan foydalanuvchining birinchi xabari (masalan, lokatsiya yoki `/start m_<id>`) saqlanadi va til tanlangach davom ettiriladi.

## Mobil ilova: rasmdan vaqtlarni o'qish (API)
`POST /api/timetable/read` (tanasi: JPEG/PNG/WebP rasm) jadval rasmidan 5 ta vaqtni o'qiydi va qaytaradi: `{times, alt, complete, warnings}`
(`alt` ikki ustunli jadvalda ikkinchi ustun). Server **hech narsa saqlamaydi**: ilova natijani foydalanuvchiga tekshirtiradi va oddiy
`PATCH` bilan saqlaydi. Rasm xotirada o'qiladi (Tesseract, Docker image'da bor) va tashlanadi. Himoya: bir qurilmadan soatiga `PHOTO_PER_HOUR`
(standart 30; bir IP'dan 4 baravar), bir vaqtda `OCR_CONCURRENCY` (standart 2) ta rasm o'qiladi, qolganiga `503 busy`; hajm `MAX_PHOTO_BYTES`
(8 MB); Tesseract yo'q bo'lsa `503 ocr_unavailable` va `/api/config` da `photo_reading: false` (ilova tugmani yashiradi).

Ovozdan o'qish (`POST /api/timetable/voice`, ilovadagi mikrofon tugmasi): bot bilan bir xil ovoz modeli (`faster-whisper`, `WITH_VOICE=1`) va bir xil qoidalar (o'zbekcha, ruscha, inglizcha; AM/PM aniqlanadi). Yozuv xotirada tanib olinadi va tashlanadi; soatlik cheklov rasmniki bilan umumiy, hajm `MAX_VOICE_BYTES` (2 MB), bir vaqtda bitta yozuv (`503 busy`); model yo'q bo'lsa `503 stt_unavailable` va `/api/config` da `voice_reading: false` (ilova tugmani yashiradi).
