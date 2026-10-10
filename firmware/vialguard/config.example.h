// Copy this file to config.h (same folder) and fill in your values.
// config.h is git-ignored so your passwords never get committed.
#pragma once

// ---- Wi-Fi (use your phone hotspot at the expo) ----
#define WIFI_SSID "YourHotspotName"
#define WIFI_PASS "YourHotspotPassword"

// ---- Firebase Realtime Database ----
// Firebase console -> Build -> Realtime Database -> copy the URL (no trailing slash)
#define FIREBASE_URL "https://your-project-default-rtdb.asia-southeast1.firebasedatabase.app"
// Leave empty while the database is in test mode. Otherwise paste a database secret.
#define FIREBASE_AUTH ""
#define DEVICE_ID "vg-001"

// ---- Telegram (create a bot with @BotFather, get chat IDs from @userinfobot) ----
// Leave the token empty to disable Telegram.
#define TELEGRAM_TOKEN ""
// Alert ladder: courier -> pharmacist -> QA head. For a demo, all three can be the same chat.
#define CHAT_COURIER "123456789"
#define CHAT_PHARMACIST "123456789"
#define CHAT_QA "123456789"

// ---- Timing ----
// Demo time scale: 360 means 1 real second counts as 6 minutes, so 30 s of hair dryer = 3 h of heat.
// Set to 1 for real-world use. The OLED and dashboard show "DEMO x360" whenever it is not 1.
#define TIME_SCALE 360
#define SAMPLE_MS 2000              // real use: 10000 (every 10 s)
#define ESCALATE_PHARMACIST_SEC 30  // real use: 300 (5 min)
#define ESCALATE_QA_SEC 60          // real use: 900 (15 min)
#define DEFAULT_DRUG "demo"         // demo | insulin | vvm7 | vvm2

// ---- Pins (match docs/WIRING.md) ----
#define PIN_DS18B20 4
#define PIN_LDR 34          // analog input
#define PIN_BUZZER 25
#define PIN_BUTTON 0        // the BOOT button on the board
#define LID_OPEN_ADC 1800   // 0-4095; check the "ldr" value on the dashboard and adjust
#define OLED_ADDR 0x3C
