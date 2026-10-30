"""Texts of the bot's extra features (nearest mosque card, subscriptions, reminders). Merged into bot.TEXTS."""

REGION_NAMES = {
    "uz": ["Toshkent shahri", "Toshkent viloyati", "Andijon", "Buxoro", "Fargʻona", "Jizzax", "Xorazm", "Namangan", "Navoiy",
           "Qashqadaryo", "Qoraqalpogʻiston", "Samarqand", "Sirdaryo", "Surxondaryo"],
    "ru": ["Ташкент (город)", "Ташкентская область", "Андижан", "Бухара", "Фергана", "Джизак", "Хорезм", "Наманган", "Навои",
           "Кашкадарья", "Каракалпакстан", "Самарканд", "Сырдарья", "Сурхандарья"],
    "en": ["Tashkent city", "Tashkent region", "Andijan", "Bukhara", "Fergana", "Jizzakh", "Khorezm", "Namangan", "Navoi",
           "Kashkadarya", "Karakalpakstan", "Samarkand", "Syrdarya", "Surkhandarya"],
}

MORE = {
    "uz": {
        "route_hint": "Xaritani oching va «Yoʻnalish»ni bosing: Telegram telefoningizdagi xarita ilovalarini taklif qiladi.", "imp_cmd": "Masjidlar bazasini yangilash", "imp_off": "Bazani yangilash bu serverda oʻchirilgan (BOT_OSM_IMPORT=0).", "imp_net": "❌ Server OpenStreetMapʼga ulana olmadi: tarmoq yopiq boʻlishi mumkin (overpass-api.de ga chiqish kerak). Baza har kecha 02:15 da baribir yangilanadi.", "imp_btn": "🔄 Masjidlar bazasini yangilash (OpenStreetMap)", "imp_start": "⏳ OpenStreetMapʼdan yangilanmoqda… Bu 1–8 daqiqa olishi mumkin, tugagach yozaman.", "imp_busy": "Yangilash allaqachon ketyapti, tugashini kuting.", "imp_done": "✅ Masjidlar bazasi yangilandi: {added} ta qoʻshildi, {updated} ta yangilandi, {unchanged} ta oʻzgarmadi, {protected_edited} ta foydalanuvchilar tahriri saqlandi, {near_existing} ta mavjudiga juda yaqin, {photos} ta rasm.", "imp_fail": "❌ Yangilab boʻlmadi ({why}). OpenStreetMap band boʻlishi mumkin, keyinroq qayta urinib koʻring.", "venue_btn": "📍 Telegram xaritasida", "copy_btn": "📋 Vaqtlarni nusxalash", "ai_notice": "🔒 Yuborgan rasm yoki ovozingiz oʻqish uchun Google (Gemini) ga joʻnatiladi va oʻqilgach saqlanmaydi.", "m": "m", "km": "km", "no_times": "Jamoat vaqtlari hali kiritilmagan.", "updated": "🕒 Yangilangan: {d}",
        "route": "🧭 Yoʻnalish", "upd_btn": "✏️ Vaqtlarni yangilash", "sub_btn": "🔔 Obuna boʻlish", "unsub_btn": "🔕 Obunani bekor qilish",
        "card_head": "Eng yaqin masjid:", "near_ask": "Eng yaqin masjidni topish uchun joylashuvingizni yuboring 👇",
        "near_none": "Yaqin atrofda masjid topilmadi.",
        "subscribed": "🔔 «{name}» masjidiga obuna boʻldingiz. Jamoat vaqtlari oʻzgarsa, sizga xabar beraman.",
        "unsubscribed": "🔕 «{name}» masjidiga obuna bekor qilindi.", "sub_already": "Siz allaqachon obuna boʻlgansiz.",
        "sub_limit": "Koʻpi bilan {n} ta masjidga obuna boʻlish mumkin. Avval baʼzilarini bekor qiling (/subs).",
        "subs_title": "🔔 Obunalaringiz (bosib bekor qilish mumkin):", "subs_none": "Hozircha obunalaringiz yoʻq. Masjid kartasidagi «🔔 Obuna boʻlish» tugmasini bosing.",
        "rem_title": "⏰ Eslatmalar\nHudud: {region}\nHar kungi xabar: {daily}\nNamoz oldidan: {before}",
        "rem_none": "tanlanmagan", "on": "yoqilgan", "off": "oʻchirilgan",
        "rem_region": "🌍 Hududni tanlash", "rem_daily": "📅 Har kungi xabar: {s}", "rem_before": "⏳ Namozdan 15 daqiqa oldin: {s}",
        "rem_pick_region": "Namoz vaqtlari qaysi hudud boʻyicha hisoblansin?", "rem_need_region": "Avval hududni tanlang.",
        "rem_saved": "✅ Saqlandi.", "rem_off_all": "🔕 Hammasini oʻchirish",
        "daily_msg": "🕌 Bugungi namoz vaqtlari · {region}\n{t}", "pre_msg": "⏰ {prayer} vaqtiga 15 daqiqa qoldi ({time}) · {region}",
        "change_msg": "🔔 «{name}» masjidida jamoat vaqtlari oʻzgardi:\n{t}", "sun": "Quyosh",
        "stop_msg": "Barcha maʼlumotlaringiz (obunalar va eslatmalar) oʻchirildi.", "stop_btn": "🗑 Maʼlumotlarimni oʻchirish",
        "sub_offer": "🔔 Bu masjid vaqtlari oʻzgarsa, xabar olish",
    },
    "ru": {
        "route_hint": "Откройте карту и нажмите «Маршрут»: Telegram предложит приложения карт на вашем телефоне.", "imp_cmd": "Обновить базу мечетей", "imp_off": "Обновление базы на этом сервере выключено (BOT_OSM_IMPORT=0).", "imp_net": "❌ Сервер не смог подключиться к OpenStreetMap: возможно, сеть закрыта (нужен выход на overpass-api.de). База всё равно обновляется каждую ночь в 02:15.", "imp_btn": "🔄 Обновить базу мечетей (OpenStreetMap)", "imp_start": "⏳ Обновляю из OpenStreetMap… Это может занять 1–8 минут, напишу, когда закончу.", "imp_busy": "Обновление уже идёт, дождитесь окончания.", "imp_done": "✅ База мечетей обновлена: добавлено {added}, обновлено {updated}, без изменений {unchanged}, правки пользователей сохранены у {protected_edited}, слишком близко к существующим {near_existing}, фото {photos}.", "imp_fail": "❌ Не удалось обновить ({why}). Возможно, OpenStreetMap занят, попробуйте позже.", "venue_btn": "📍 На карте Telegram", "copy_btn": "📋 Скопировать время", "ai_notice": "🔒 Отправленные фото или голос передаются в Google (Gemini) для распознавания и после обработки не сохраняются.", "m": "м", "km": "км", "no_times": "Время джамаата ещё не указано.", "updated": "🕒 Обновлено: {d}",
        "route": "🧭 Маршрут", "upd_btn": "✏️ Обновить время", "sub_btn": "🔔 Подписаться", "unsub_btn": "🔕 Отписаться",
        "card_head": "Ближайшая мечеть:", "near_ask": "Чтобы найти ближайшую мечеть, отправьте свою геопозицию 👇",
        "near_none": "Поблизости мечетей не найдено.",
        "subscribed": "🔔 Вы подписались на мечеть «{name}». Сообщу, если время джамаата изменится.",
        "unsubscribed": "🔕 Подписка на мечеть «{name}» отменена.", "sub_already": "Вы уже подписаны.",
        "sub_limit": "Можно подписаться не более чем на {n} мечетей. Сначала отмените некоторые (/subs).",
        "subs_title": "🔔 Ваши подписки (нажмите, чтобы отписаться):", "subs_none": "Подписок пока нет. Нажмите «🔔 Подписаться» на карточке мечети.",
        "rem_title": "⏰ Напоминания\nРегион: {region}\nЕжедневное сообщение: {daily}\nПеред намазом: {before}",
        "rem_none": "не выбран", "on": "включено", "off": "выключено",
        "rem_region": "🌍 Выбрать регион", "rem_daily": "📅 Ежедневное сообщение: {s}", "rem_before": "⏳ За 15 минут до намаза: {s}",
        "rem_pick_region": "По какому региону считать время намазов?", "rem_need_region": "Сначала выберите регион.",
        "rem_saved": "✅ Сохранено.", "rem_off_all": "🔕 Выключить всё",
        "daily_msg": "🕌 Время намазов на сегодня · {region}\n{t}", "pre_msg": "⏰ До намаза {prayer} 15 минут ({time}) · {region}",
        "change_msg": "🔔 В мечети «{name}» изменилось время джамаата:\n{t}", "sun": "Восход",
        "stop_msg": "Все ваши данные (подписки и напоминания) удалены.", "stop_btn": "🗑 Удалить мои данные",
        "sub_offer": "🔔 Получать сообщения, если время этой мечети изменится",
    },
    "en": {
        "route_hint": "Open the map and tap “Directions”: Telegram offers the maps apps on your phone.", "imp_cmd": "Update the mosque database", "imp_off": "Updating the database is switched off on this server (BOT_OSM_IMPORT=0).", "imp_net": "❌ The server could not reach OpenStreetMap: the network may be closed (it needs overpass-api.de). The database is still updated every night at 02:15.", "imp_btn": "🔄 Update the mosque database (OpenStreetMap)", "imp_start": "⏳ Updating from OpenStreetMap… This can take 1–8 minutes, I will write when it is done.", "imp_busy": "An update is already running, please wait for it.", "imp_done": "✅ Mosque database updated: {added} added, {updated} updated, {unchanged} unchanged, {protected_edited} kept as edited by users, {near_existing} too close to an existing one, {photos} photos.", "imp_fail": "❌ Could not update ({why}). OpenStreetMap may be busy, try again later.", "venue_btn": "📍 On the Telegram map", "copy_btn": "📋 Copy the times", "ai_notice": "🔒 A photo or voice message you send is passed to Google (Gemini) to be read and is not kept afterwards.", "m": "m", "km": "km", "no_times": "Congregation times have not been entered yet.", "updated": "🕒 Updated: {d}",
        "route": "🧭 Directions", "upd_btn": "✏️ Update the times", "sub_btn": "🔔 Subscribe", "unsub_btn": "🔕 Unsubscribe",
        "card_head": "Nearest mosque:", "near_ask": "Share your location to find the nearest mosque 👇",
        "near_none": "No mosque found nearby.",
        "subscribed": "🔔 You subscribed to “{name}”. I will message you when its congregation times change.",
        "unsubscribed": "🔕 Unsubscribed from “{name}”.", "sub_already": "You are already subscribed.",
        "sub_limit": "You can subscribe to at most {n} mosques. Cancel some first (/subs).",
        "subs_title": "🔔 Your subscriptions (tap to unsubscribe):", "subs_none": "No subscriptions yet. Tap “🔔 Subscribe” on a mosque card.",
        "rem_title": "⏰ Reminders\nRegion: {region}\nDaily message: {daily}\nBefore each prayer: {before}",
        "rem_none": "not chosen", "on": "on", "off": "off",
        "rem_region": "🌍 Choose region", "rem_daily": "📅 Daily message: {s}", "rem_before": "⏳ 15 minutes before prayer: {s}",
        "rem_pick_region": "Which region should the prayer times be calculated for?", "rem_need_region": "Choose a region first.",
        "rem_saved": "✅ Saved.", "rem_off_all": "🔕 Turn everything off",
        "daily_msg": "🕌 Today's prayer times · {region}\n{t}", "pre_msg": "⏰ 15 minutes until {prayer} ({time}) · {region}",
        "change_msg": "🔔 The congregation times of “{name}” changed:\n{t}", "sun": "Sunrise",
        "stop_msg": "All your data (subscriptions and reminders) has been deleted.", "stop_btn": "🗑 Delete my data",
        "sub_offer": "🔔 Get a message when this mosque's times change",
    },
}


# The bot's profile: the description is shown in an empty chat before /start (<= 512 characters), the short one on the profile and with a
# shared link (<= 120). Uzbek is the default for every other language.
DESCRIPTIONS = {
    "uz": ("🕌 Masjid xaritasi: eng yaqin masjidni toping, jamoat namozi vaqtlarini koʻring va yangilang.\n\n"
           "📍 Joylashuvingizni yuboring, masjidni tanlang, keyin vaqtlar jadvalining rasmini yuboring yoki vaqtlarni ovozli xabar bilan ayting. "
           "🔔 Masjidga obuna boʻlsangiz, vaqtlar oʻzgarganda xabar beraman; ⏰ namoz oldidan eslatma ham bor.",
           "Eng yaqin masjid va jamoat namozi vaqtlari. Vaqtlarni rasm yoki ovoz bilan yangilang."),
    "ru": ("🕌 Карта мечетей: найдите ближайшую мечеть, смотрите и обновляйте время коллективного намаза.\n\n"
           "📍 Отправьте геопозицию, выберите мечеть, затем пришлите фото расписания или назовите время голосом. "
           "🔔 Подпишитесь на мечеть, и я сообщу, когда время изменится; ⏰ есть и напоминания перед намазом.",
           "Ближайшая мечеть и время коллективного намаза. Обновляйте время фото или голосом."),
    "en": ("🕌 Mosque map: find the nearest mosque, see and update its congregation prayer times.\n\n"
           "📍 Share your location, choose the mosque, then send a photo of its timetable or say the times in a voice message. "
           "🔔 Subscribe to a mosque and I will tell you when its times change; ⏰ reminders before prayers too.",
           "The nearest mosque and its congregation prayer times. Update them with a photo or your voice."),
}

