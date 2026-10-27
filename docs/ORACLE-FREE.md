# Bepul va ishonchli: Oracle Cloud "Always Free" + Docker

Bepul, uxlamaydigan, doimiy ishlaydigan VM. Loyihaning Docker Compose'i (Postgres, web, bot, osm-sync, Caddy HTTPS) shu yerda
o'zgarishsiz ishlaydi. Tafsilotlar o'zgarib turadi: ro'yxatdan o'tishdan oldin https://www.oracle.com/cloud/free/ ni tekshiring.

## 1. Hisob
- https://signup.cloud.oracle.com — email + **bank kartasi** (faqat shaxsni tasdiqlash uchun, "Always Free" resurslar uchun pul olinmaydi).
- **Home region** keyinroq o'zgarmaydi. Bo'sh quvvat ko'proq bo'lgan regionni tanlang (masalan Frankfurt yoki Amsterdam).
- Hisobni "Pay As You Go"ga o'tkazmaslik ham mumkin; Always Free resurslar baribir bepul qoladi.

## 2. VM yaratish
Compute → Instances → Create:
- Image: **Ubuntu 24.04** (yoki 22.04), Shape: **VM.Standard.A1.Flex (ARM)**, **2 OCPU / 12 GB** (bepul chegara jami 4 OCPU / 24 GB).
  Ovozdan o'qish modeli uchun RAM kerak, shuning uchun 12 GB qulay.
- "Out of capacity" xatosi chiqsa: boshqa availability domain, yoki bir necha soatdan keyin qayta urinib ko'ring.
- SSH kalit: kompyuteringizdagi `~/.ssh/id_ed25519.pub` ni yuklang. Boot volume 50–100 GB.
- Public IP: **Reserved public IP** oling (Networking → Reserved public IPs) va VM'ga biriktiring: IP o'zgarmaydi.

## 3. Portlar (ikki joyda!)
1. Oracle konsoli: Networking → VCN → Security Lists → Default → **Ingress Rules**: `0.0.0.0/0` TCP **80** va **443**.
2. VM ichida Oracle'ning Ubuntu image'i iptables'da hamma narsani yopadi. Docker o'zi e'lon qilgan portlarni ochadi,
   lekin sayt tashqaridan ochilmasa:
   ```bash
   sudo iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
   sudo apt-get install -y iptables-persistent && sudo netfilter-persistent save
   ```
5432 (Postgres) va 8080 ochilmaydi.

## 4. Domen (bepul)
HTTPS va Telegram Mini App uchun domen kerak. Bepul: https://www.duckdns.org → `nomingiz.duckdns.org` → IP = VM'ning reserved IP'si.
Tekshirish: `dig +short nomingiz.duckdns.org`.

## 5. O'rnatish (kompyuteringizdan)
`.env.server` ni `docs/SERVER-SETUP.md` (C bo'limi) bo'yicha to'ldiring: `DOMAIN=nomingiz.duckdns.org`,
`COMPOSE_PROFILES=osm,https,bot`, `TELEGRAM_BOT_TOKEN`, `ADMIN_TG_IDS`, `OSM_CONTACT`. Keyin:
```bash
./deploy/push.sh ubuntu@RESERVED_IP --run
./deploy/verify.sh https://nomingiz.duckdns.org
```
Image serverning o'zida (ARM uchun) quriladi (`--build`), birinchi marta 5–10 daqiqa. Docker, zaxira cron'i va qayta yonganda
avtomatik ko'tarilish `bootstrap.sh` tomonidan o'rnatiladi. Ovozdan o'qish uchun `WITH_VOICE=1` (standart) qoladi.

## 6. Ishonchlilik
- **Idle VM'ni o'chirish:** Oracle uzoq vaqt (taxminan 7 kun) juda kam ishlatilgan Always Free VM'ni to'xtatishi mumkin. Bot, osm-sync va
  foydalanuvchilar yetarli yuk beradi; baribir `docker compose ps` ni ora-sira ko'ring va zaxirani saqlang.
- **Zaxira:** kunlik 03:15, 14 kun (`./deploy/backup-docker.sh`). Haftada bir `backups/` ni kompyuteringizga ko'chiring:
  `rsync -az ubuntu@IP:~/masjid-uz/backups/ ./backups/`. Faqat VM'dagi zaxiraga ishonmang.
- **Yangilash:** kompyuterda `./deploy/push.sh ubuntu@IP --run`.
- **Monitoring (bepul):** https://uptimerobot.com bilan `https://<domen>/api/health` ni 5 daqiqada tekshiring.

## Muqobil (kam ishonchli)
Render (veb) + Neon (Postgres): kartasiz, lekin veb-servis 15 daqiqadan keyin uxlaydi va bot o'sha paytda javob bermaydi,
ovoz modeli sig'maydi. Doimiy xizmat uchun Oracle VM yaxshiroq.
