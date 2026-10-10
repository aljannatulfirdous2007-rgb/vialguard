// VialGuard firmware for ESP32
// Smart cold-chain package: logs temperature, predicts drug viability, alerts the right person,
// and keeps a tamper-evident (hash-chained) record of the journey.
//
// The viability logic mirrors model/vialguard_model.py exactly. Change it there first.
// Wiring: docs/WIRING.md. Setup: README.md.

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <mbedtls/sha256.h>
#include <time.h>
#include <math.h>
#include "config.h"

#define FW_VERSION "1.0"

// ------------------------------------------------------------------ model constants (see vialguard_model.py)
const double T_REF_K = 298.15;
const double MKT_EA_OVER_R = 10000.0;
const float UPPER_LIMIT_C = 8.0;
const float FREEZE_LIMIT_C = 0.0;
const int FREEZE_CONFIRM_READINGS = 2;
const float WARN_PCT = 50.0;
const float CRIT_PCT = 20.0;

struct Profile {
  const char *key;
  const char *name;
  double budgetHoursAt25c;
  double eaOverR;
  bool freezeSensitive;
};

const Profile PROFILES[] = {
  {"demo", "Demo biologic", 48.0, 10000.0, true},
  {"insulin", "Insulin (unopened)", 28 * 24.0, 10000.0, true},
  {"vvm7", "Vaccine VVM7", 45 * 24.0, 14340.0, true},
  {"vvm2", "Vaccine VVM2", 309.0, 14340.0, false},
};
const int NUM_PROFILES = sizeof(PROFILES) / sizeof(PROFILES[0]);

// ------------------------------------------------------------------ hardware
OneWire oneWire(PIN_DS18B20);
DallasTemperature sensor(&oneWire);
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oledOk = false;

// ------------------------------------------------------------------ tracker state
int profileIdx = 0;
double budgetUsedH = 0;
bool frozen = false;
int belowFreezeCount = 0;
double mktSum = 0;
long mktN = 0;
float lastTemp = NAN;
bool lidOpen = false;
int lastLdr = 0;
bool inExcursion = false;
bool warned50 = false, warned20 = false, discardSent = false;

// ------------------------------------------------------------------ hash-chained log
struct Rec {
  uint32_t seq;
  uint32_t ts;
  int32_t t;      // temperature in centi-degrees
  int32_t b;      // budget in basis points (10000 = 100.00 %)
  uint8_t lid;
  char ev[64];
  char prev[65];
  char h[65];
};
const int QUEUE_LEN = 120;      // about 4 minutes of readings at 2 s, buffered while offline
Rec queue[QUEUE_LEN];
int qHead = 0, qCount = 0;
uint32_t seqNo = 0;
char prevHash[65] = "GENESIS";
char shipId[24];
String pendingEvents;

// ------------------------------------------------------------------ alerts
const char *CHATS[3] = {CHAT_COURIER, CHAT_PHARMACIST, CHAT_QA};
const char *ROLES[3] = {"Courier", "Pharmacist", "QA Head"};
bool alertActive = false;
bool alertAcked = false;
int alertLevel = -1;       // 0 courier, 1 pharmacist, 2 QA
int alertSeverity = 0;     // 1 excursion/warning, 2 critical, 3 discard
String alertReason;
unsigned long alertLevelSince = 0;
long tgOffset = 0;

// ------------------------------------------------------------------ timers
unsigned long lastSample = 0, lastPush = 0, lastCmdPoll = 0, lastTgPoll = 0, lastWifiTry = 0, lastBeep = 0;
unsigned long buttonDownAt = 0;
bool buttonWasDown = false;

// ================================================================== model
double rateAt(double tempC, double eaOverR) {
  return exp(-eaOverR * (1.0 / (tempC + 273.15) - 1.0 / T_REF_K));
}

bool validReading(float t) { return t >= -55.0 && t <= 84.0; }

double budgetPct() {
  double p = 100.0 * (1.0 - budgetUsedH / PROFILES[profileIdx].budgetHoursAt25c);
  return p < 0 ? 0 : p;
}

double mktC() {
  if (mktN == 0) return NAN;
  return MKT_EA_OVER_R / -log(mktSum / mktN) - 273.15;
}

// negative = safe indefinitely (in range)
double hoursLeft(float t) {
  if (frozen || budgetPct() <= 0) return 0;
  if (t <= UPPER_LIMIT_C) return -1;
  const Profile &p = PROFILES[profileIdx];
  return (p.budgetHoursAt25c - budgetUsedH) / rateAt(t, p.eaOverR);
}

const char *verdict() {
  if (frozen) return "DISCARD_FROZEN";
  double p = budgetPct();
  if (p <= 0) return "DISCARD_HEAT";
  if (p < CRIT_PCT) return "USE_IMMEDIATELY";
  if (p < WARN_PCT) return "USE_SOON";
  return "OK";
}

void trackerUpdate(float t, double dtHours) {
  if (!validReading(t)) return;
  const Profile &p = PROFILES[profileIdx];
  mktSum += exp(-MKT_EA_OVER_R / (t + 273.15));
  mktN++;
  if (t > UPPER_LIMIT_C) budgetUsedH += rateAt(t, p.eaOverR) * dtHours;
  if (t <= FREEZE_LIMIT_C) {
    belowFreezeCount++;
    if (p.freezeSensitive && belowFreezeCount >= FREEZE_CONFIRM_READINGS) frozen = true;
  } else {
    belowFreezeCount = 0;
  }
}

void trackerReset() {
  budgetUsedH = 0; frozen = false; belowFreezeCount = 0; mktSum = 0; mktN = 0;
  inExcursion = false; warned50 = warned20 = discardSent = false;
}

// ================================================================== helpers
void addEvent(const char *ev) {
  if (pendingEvents.length()) pendingEvents += ",";
  pendingEvents += ev;
  Serial.printf("[event] %s\n", ev);
}

uint32_t nowEpoch() {
  time_t t = time(nullptr);
  return t > 1600000000 ? (uint32_t)t : 0;
}

void sha256Hex(const String &msg, char out[65]) {
  unsigned char d[32];
  mbedtls_sha256((const unsigned char *)msg.c_str(), msg.length(), d, 0);
  for (int i = 0; i < 32; i++) sprintf(out + i * 2, "%02x", d[i]);
  out[64] = 0;
}

String urlEncode(const String &s) {
  String o;
  const char *hex = "0123456789ABCDEF";
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (isalnum((unsigned char)c) || c == '-' || c == '_' || c == '.' || c == '~') o += c;
    else { o += '%'; o += hex[(c >> 4) & 0xF]; o += hex[c & 0xF]; }
  }
  return o;
}

String fbUrl(const String &path) {
  String u = String(FIREBASE_URL) + path + ".json";
  if (strlen(FIREBASE_AUTH)) u += String("?auth=") + FIREBASE_AUTH;
  return u;
}

bool online() { return WiFi.status() == WL_CONNECTED; }

// method: "GET", "PUT", "PATCH". Returns HTTP code (<=0 on failure).
int https(const char *method, const String &url, const String &body, String *resp) {
  if (!online()) return -1;
  WiFiClientSecure secure;
  secure.setInsecure();   // demo build: skip certificate pinning
  WiFiClient plain;       // http:// = laptop running simulator/local_server.py (no internet needed)
  HTTPClient http;
  http.setTimeout(6000);
  bool ok = url.startsWith("https://") ? http.begin(secure, url) : http.begin(plain, url);
  if (!ok) return -1;
  int code;
  if (!strcmp(method, "GET")) code = http.GET();
  else {
    http.addHeader("Content-Type", "application/json");
    code = http.sendRequest(method, body);
  }
  if (resp && code > 0) *resp = http.getString();
  http.end();
  return code;
}

// ================================================================== telegram
bool tgEnabled() { return strlen(TELEGRAM_TOKEN) > 0; }

void tgSend(const char *chat, const String &text) {
  if (!tgEnabled()) return;
  String url = String("https://api.telegram.org/bot") + TELEGRAM_TOKEN + "/sendMessage?chat_id=" + chat + "&text=" + urlEncode(text);
  int code = https("GET", url, "", nullptr);
  Serial.printf("[telegram] -> %s : %d\n", chat, code);
}

String statusText() {
  char b[200];
  double hl = hoursLeft(lastTemp);
  String hls = hl < 0 ? String("safe while 2-8 C") : String(hl, 1) + " h at current temp";
  snprintf(b, sizeof(b), "%s | %.1f C | %.0f%% budget left | %s | %s", PROFILES[profileIdx].name, lastTemp, budgetPct(), verdict(), hls.c_str());
  return String(b);
}

void raiseAlert(int severity, const String &reason) {
  if (alertActive && !alertAcked && severity <= alertSeverity) return;
  alertActive = true; alertAcked = false; alertSeverity = severity; alertReason = reason;
  alertLevel = 0; alertLevelSince = millis();
  addEvent("ALERT_L0");
  tgSend(CHATS[0], String("VIALGUARD ALERT (") + DEVICE_ID + ")\n" + reason + "\n" + statusText() + "\nReply /ack to acknowledge.");
}

void escalateIfNeeded() {
  if (!alertActive || alertAcked || alertLevel >= 2) return;
  unsigned long waitMs = (alertLevel == 0 ? ESCALATE_PHARMACIST_SEC : ESCALATE_QA_SEC - ESCALATE_PHARMACIST_SEC) * 1000UL;
  if (millis() - alertLevelSince < waitMs) return;
  alertLevel++; alertLevelSince = millis();
  addEvent(alertLevel == 1 ? "ALERT_L1" : "ALERT_L2");
  tgSend(CHATS[alertLevel], String("ESCALATED to ") + ROLES[alertLevel] + ": nobody acknowledged.\n" + alertReason + "\n" + statusText() + "\nReply /ack to acknowledge.");
}

void acknowledge(const char *by) {
  if (!alertActive || alertAcked) return;
  alertAcked = true;
  addEvent("ACK");
  for (int i = 0; i <= alertLevel; i++) tgSend(CHATS[i], String("Acknowledged via ") + by + ". " + statusText());
}

void tgPoll() {
  if (!tgEnabled()) return;
  String resp;
  String url = String("https://api.telegram.org/bot") + TELEGRAM_TOKEN + "/getUpdates?timeout=0&offset=" + String(tgOffset);
  if (https("GET", url, "", &resp) != 200) return;
  int idx = 0;
  while ((idx = resp.indexOf("\"update_id\":", idx)) >= 0) {
    idx += 12;
    long id = resp.substring(idx, resp.indexOf(',', idx)).toInt();
    if (id >= tgOffset) tgOffset = id + 1;
  }
  if (resp.indexOf("/ack") >= 0) acknowledge("Telegram");
  if (resp.indexOf("/status") >= 0) tgSend(CHATS[0], statusText());
}

// ================================================================== firebase
String recJson(const Rec &r) {
  char b[400];
  snprintf(b, sizeof(b), "{\"seq\":%u,\"ts\":%u,\"t\":%d,\"b\":%d,\"lid\":%u,\"ev\":\"%s\",\"prev\":\"%s\",\"h\":\"%s\"}",
           (unsigned)r.seq, (unsigned)r.ts, (int)r.t, (int)r.b, (unsigned)r.lid, r.ev, r.prev, r.h);
  return String(b);
}

String stateJson() {
  char b[1000];
  double hl = hoursLeft(lastTemp);
  double m = mktC();
  snprintf(b, sizeof(b),
           "{\"seq\":%u,\"ts\":%u,\"temp\":%.2f,\"budget\":%.2f,\"verdict\":\"%s\",\"drug\":\"%s\",\"drug_name\":\"%s\","
           "\"mkt\":%s,\"hours_left\":%.2f,\"lid\":%d,\"ldr\":%d,\"excursion\":%d,\"frozen\":%d,\"alert_level\":%d,\"acked\":%d,"
           "\"alert_reason\":\"%s\",\"scale\":%d,\"ship\":\"%s\",\"rssi\":%d,\"pending\":%d,\"fw\":\"%s\"}",
           (unsigned)seqNo, (unsigned)nowEpoch(), isnan(lastTemp) ? 0.0 : lastTemp, budgetPct(), verdict(), PROFILES[profileIdx].key,
           PROFILES[profileIdx].name, isnan(m) ? "null" : String(m, 2).c_str(), hl, lidOpen, lastLdr, inExcursion, frozen,
           alertActive ? alertLevel : -1, alertAcked, alertReason.c_str(), TIME_SCALE, shipId, WiFi.RSSI(), qCount, FW_VERSION);
  return String(b);
}

// Sends the live state plus up to 15 queued readings in a single multi-path PATCH.
void pushToFirebase() {
  if (!online()) return;
  int n = qCount < 15 ? qCount : 15;
  String body = "{\"state\":" + stateJson();
  for (int i = 0; i < n; i++) {
    const Rec &r = queue[(qHead + i) % QUEUE_LEN];
    char key[64];
    snprintf(key, sizeof(key), "shipments/%s/readings/%08u", shipId, (unsigned)r.seq);
    body += ",\"" + String(key) + "\":" + recJson(r);
  }
  body += "}";
  int code = https("PATCH", fbUrl(String("/devices/") + DEVICE_ID), body, nullptr);
  if (code == 200) { qHead = (qHead + n) % QUEUE_LEN; qCount -= n; }
  else Serial.printf("[firebase] push failed: %d (queued %d)\n", code, qCount);
}

void startShipment(const char *reason) {
  trackerReset();
  uint32_t e = nowEpoch();
  if (e) snprintf(shipId, sizeof(shipId), "s%u", (unsigned)e);
  else snprintf(shipId, sizeof(shipId), "b%08lx", (unsigned long)esp_random());
  seqNo = 0;
  strcpy(prevHash, "GENESIS");
  qHead = qCount = 0;
  alertActive = alertAcked = false; alertLevel = -1; alertSeverity = 0; alertReason = "";
  addEvent(reason);
}

void pollCommands() {
  String resp;
  if (https("GET", fbUrl(String("/devices/") + DEVICE_ID + "/cmd"), "", &resp) != 200 || resp == "null") return;
  bool changed = false;
  if (resp.indexOf("\"ack\":true") >= 0) { acknowledge("dashboard"); changed = true; }
  int d = resp.indexOf("\"drug\":\"");
  if (d >= 0) {
    String key = resp.substring(d + 8, resp.indexOf('"', d + 8));
    for (int i = 0; i < NUM_PROFILES; i++) if (key == PROFILES[i].key) profileIdx = i;
    startShipment("NEW_SHIPMENT");
    changed = true;
  } else if (resp.indexOf("\"reset\":true") >= 0) {
    startShipment("NEW_SHIPMENT");
    changed = true;
  }
  if (changed || resp.length()) https("PUT", fbUrl(String("/devices/") + DEVICE_ID + "/cmd"), "null", nullptr);
}

// ================================================================== sampling
void appendRecord(float t) {
  Rec r;
  r.seq = seqNo;
  r.ts = nowEpoch();
  r.t = validReading(t) ? (int32_t)lround(t * 100.0) : -12700;
  r.b = (int32_t)lround(budgetPct() * 100.0);
  r.lid = lidOpen ? 1 : 0;
  strncpy(r.ev, pendingEvents.c_str(), sizeof(r.ev) - 1);
  r.ev[sizeof(r.ev) - 1] = 0;
  strcpy(r.prev, prevHash);
  String msg = String(r.seq) + "|" + String(r.ts) + "|" + String(r.t) + "|" + String(r.b) + "|" + String(r.lid) + "|" + String(r.ev) + "|" + String(r.prev);
  sha256Hex(msg, r.h);
  strcpy(prevHash, r.h);
  pendingEvents = "";
  seqNo++;
  if (qCount == QUEUE_LEN) { qHead = (qHead + 1) % QUEUE_LEN; qCount--; }   // drop oldest if offline too long
  queue[(qHead + qCount) % QUEUE_LEN] = r;
  qCount++;
}

void readLid() {
  lastLdr = analogRead(PIN_LDR);
  bool open = lidOpen ? lastLdr > LID_OPEN_ADC - 150 : lastLdr > LID_OPEN_ADC + 150;   // hysteresis
  if (open != lidOpen) {
    lidOpen = open;
    addEvent(open ? "LID_OPEN" : "LID_CLOSE");
  }
}

void sample() {
  sensor.requestTemperatures();
  float t = sensor.getTempCByIndex(0);
  readLid();
  if (!validReading(t)) {
    addEvent("SENSOR_ERROR");
    appendRecord(t);
    return;
  }
  lastTemp = t;
  // use the real elapsed time: network calls can stretch the loop beyond SAMPLE_MS
  static unsigned long lastSampleAt = 0;
  unsigned long nowMs = millis();
  unsigned long elapsed = lastSampleAt ? nowMs - lastSampleAt : SAMPLE_MS;
  lastSampleAt = nowMs;
  double dtHours = (elapsed / 3600000.0) * TIME_SCALE;
  bool wasFrozen = frozen;
  trackerUpdate(t, dtHours);

  if (t > UPPER_LIMIT_C && !inExcursion) {
    inExcursion = true;
    addEvent("EXCURSION_START");
    raiseAlert(1, String("Temperature excursion: ") + String(t, 1) + " C (limit 8 C)");
  } else if (t <= UPPER_LIMIT_C && inExcursion) {
    inExcursion = false;
    addEvent("EXCURSION_END");
    if (alertActive) for (int i = 0; i <= alertLevel; i++) tgSend(CHATS[i], String("Back in range. ") + statusText());
  }
  if (frozen && !wasFrozen) {
    addEvent("FROZEN");
    raiseAlert(3, String("FROZEN at ") + String(t, 1) + " C: irreversible damage. DISCARD.");
  }
  double p = budgetPct();
  if (p < WARN_PCT && !warned50) { warned50 = true; addEvent("WARN_50"); raiseAlert(1, "Stability budget below 50%. Use soon."); }
  if (p < CRIT_PCT && !warned20) { warned20 = true; addEvent("CRIT_20"); raiseAlert(2, "Stability budget below 20%. Use immediately or discard."); }
  if (p <= 0 && !discardSent) { discardSent = true; addEvent("DISCARD"); raiseAlert(3, "Stability budget exhausted by heat. DISCARD."); }
  appendRecord(t);
  Serial.println(statusText());
}

// ================================================================== display, buzzer, button
void drawOled() {
  if (!oledOk) return;
  const char *v = verdict();
  bool discard = !strncmp(v, "DISCARD", 7);
  oled.clearDisplay();
  oled.fillScreen(discard ? SSD1306_WHITE : SSD1306_BLACK);
  oled.setTextColor(discard ? SSD1306_BLACK : SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print(PROFILES[profileIdx].key);
  if (TIME_SCALE != 1) { oled.print(" DEMO x"); oled.print(TIME_SCALE); }
  oled.setCursor(104, 0);
  oled.print(online() ? "W" : "-");
  oled.print(lidOpen ? "L" : " ");
  oled.print(alertActive && !alertAcked ? "!" : " ");
  oled.setTextSize(3);
  oled.setCursor(0, 14);
  if (discard) oled.print("DISCARD");
  else { oled.print((int)lround(budgetPct())); oled.print("%"); }
  oled.setTextSize(1);
  oled.setCursor(0, 42);
  if (!isnan(lastTemp)) { oled.print(lastTemp, 1); oled.print(" C  "); }
  oled.print(frozen ? "FROZEN" : (inExcursion ? "EXCURSION" : "in range"));
  oled.setCursor(0, 54);
  double hl = hoursLeft(lastTemp);
  if (discard) oled.print(frozen ? "freeze damage" : "heat damage");
  else if (hl < 0) oled.print("safe while 2-8 C");
  else { oled.print("use within "); oled.print(hl, 1); oled.print(" h"); }
  oled.display();
}

void buzzer() {
  bool unacked = alertActive && !alertAcked;
  bool discard = !strncmp(verdict(), "DISCARD", 7);
  unsigned long period = discard ? 600 : 2000;
  if (unacked && millis() - lastBeep > period) {
    lastBeep = millis();
    digitalWrite(PIN_BUZZER, HIGH);
    delay(discard ? 250 : 80);
    digitalWrite(PIN_BUZZER, LOW);
  }
}

// short press = acknowledge alert; hold 2 s = next drug profile + new shipment
void handleButton() {
  bool down = digitalRead(PIN_BUTTON) == LOW;
  if (down && !buttonWasDown) buttonDownAt = millis();
  if (!down && buttonWasDown) {
    if (millis() - buttonDownAt > 2000) {
      profileIdx = (profileIdx + 1) % NUM_PROFILES;
      startShipment("NEW_SHIPMENT");
    } else {
      acknowledge("button");
    }
  }
  buttonWasDown = down;
}

void ensureWifi() {
  if (online() || millis() - lastWifiTry < 10000) return;
  lastWifiTry = millis();
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

// ================================================================== setup / loop
void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  analogReadResolution(12);
  sensor.begin();
  sensor.setResolution(11);
  Wire.begin();
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  if (oledOk) { oled.clearDisplay(); oled.setTextColor(SSD1306_WHITE); oled.setTextSize(2); oled.setCursor(0, 20); oled.print("VialGuard"); oled.display(); }

  for (int i = 0; i < NUM_PROFILES; i++) if (!strcmp(PROFILES[i].key, DEFAULT_DRUG)) profileIdx = i;

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  lastWifiTry = millis();
  for (int i = 0; i < 40 && !online(); i++) delay(250);
  configTime(19800, 0, "pool.ntp.org", "time.google.com");   // IST, UTC+5:30
  for (int i = 0; i < 20 && !nowEpoch(); i++) delay(250);

  if (tgEnabled() && online()) {   // skip Telegram messages sent before boot
    String resp;
    https("GET", String("https://api.telegram.org/bot") + TELEGRAM_TOKEN + "/getUpdates?offset=-1", "", &resp);
    int idx = resp.lastIndexOf("\"update_id\":");
    if (idx >= 0) tgOffset = resp.substring(idx + 12, resp.indexOf(',', idx + 12)).toInt() + 1;
  }
  startShipment("BOOT");
  Serial.printf("VialGuard %s ready. Device %s, drug %s, shipment %s\n", FW_VERSION, DEVICE_ID, PROFILES[profileIdx].key, shipId);
}

void loop() {
  unsigned long now = millis();
  handleButton();
  if (now - lastSample >= SAMPLE_MS) { lastSample = now; sample(); drawOled(); }
  escalateIfNeeded();
  buzzer();
  ensureWifi();
  if (now - lastPush >= 2000) { lastPush = now; pushToFirebase(); }
  if (now - lastCmdPoll >= 4000) { lastCmdPoll = now; pollCommands(); }
  if (alertActive && !alertAcked && now - lastTgPoll >= 4000) { lastTgPoll = now; tgPoll(); }
  else if (now - lastTgPoll >= 15000) { lastTgPoll = now; tgPoll(); }
}
