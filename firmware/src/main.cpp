#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_system.h>
#include "motor_control.h"

#if __has_include("robot_config.h")
#include "robot_config.h"
#else
#include "robot_config.example.h"
#endif

namespace {
constexpr char kFirmwareVersion[] = "0.1.0-phase1";
constexpr uint32_t kPollMs = 500;
constexpr uint32_t kWifiRetryMs = 10000;
constexpr uint32_t kMaxRetryMs = 10000;
String bootId;
String sessionId;
uint32_t sequence = 0;
uint32_t nextContactAt = 0;
uint32_t lastWifiAttempt = 0;
uint32_t retryMs = kPollMs;
bool wifiWasConnected = false;

bool due(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}

String createBootId() {
  uint8_t bytes[16];
  esp_fill_random(bytes, sizeof(bytes));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  char result[37];
  snprintf(result, sizeof(result),
           "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
           bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
           bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]);
  return String(result);
}

bool contactServer() {
  const bool registering = sessionId.isEmpty();
  JsonDocument request;
  request["protocolVersion"] = 1;
  request["bootId"] = bootId;
  if (registering) {
    request["robotId"] = ROBOT_ID;
    request["firmwareVersion"] = kFirmwareVersion;
  } else {
    request["sessionId"] = sessionId;
    request["sequence"] = ++sequence;
    request["status"] = "IDLE";
  }
  String body;
  serializeJson(request, body);
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(1000);
  http.setTimeout(1000);
  const String path = registering ? "/api/robot-agent/register" : "/api/robot-agent/heartbeat";
  if (!http.begin(client, String(SERVER_URL) + path)) {
    Serial.println("HTTP setup failed; check SERVER_URL.");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + ROBOT_TOKEN);
  const int code = http.POST(body);
  bool valid = false;
  if (code < 0) {
    Serial.printf("HTTP transport error %d: %s; check server address and LAN access.\n",
                  code, HTTPClient::errorToString(code).c_str());
  } else if (code != 200) {
    Serial.printf("HTTP status %d on %s\n", code, path.c_str());
    if (code == 401 || code == 403) {
      Serial.println("Check robot ID and token against server configuration.");
    }
  }
  if (code == 200 && http.getSize() > 0 && http.getSize() <= 1024) {
    JsonDocument response;
    const auto error = deserializeJson(response, http.getString());
    if (error) Serial.printf("Invalid response JSON: %s\n", error.c_str());
    const String receivedSession = response["sessionId"] | "";
    valid = !error && response["protocolVersion"].is<int>() &&
            response["protocolVersion"].as<int>() == 1 &&
            response["motorsEnabled"].is<bool>() && !response["motorsEnabled"].as<bool>() &&
            response["status"].as<String>() == "IDLE" && receivedSession.length() == 36 &&
            (registering || receivedSession == sessionId);
    if (!valid && !error) Serial.println("Response fields do not match protocol version 1.");
    if (valid && registering) {
      sessionId = receivedSession;
      sequence = 0;
      Serial.println("Server registration accepted; motors remain stopped.");
    }
  } else if (code == 200) {
    Serial.printf("Rejected response size: %d bytes.\n", http.getSize());
  } else if (code == 409 || code == 401 || code == 403) {
    sessionId = "";
    sequence = 0;
  }
  http.end();
  return valid;
}
}  // namespace

void setup() {
  initializeMotors();
  Serial.begin(115200);
  if (!ROBOT_CONFIGURED) {
    Serial.println("Configure robot_config.h first; motors remain stopped.");
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  bootId = createBootId();
  lastWifiAttempt = millis();
}

void loop() {
  // Phase 1 has no movement commands. HTTP may block only while motors are stopped.
  // Phase 2 must isolate networking before enabling any motor execution.
  stopMotors();
  if (!ROBOT_CONFIGURED) {
    delay(1);
    return;
  }
  const uint32_t now = millis();
  const bool wifiConnected = WiFi.status() == WL_CONNECTED;
  if (wifiConnected != wifiWasConnected) {
    Serial.println(wifiConnected ? "Wi-Fi connected." : "Wi-Fi disconnected; motors remain stopped.");
    if (wifiConnected) Serial.printf("Robot IP: %s\n", WiFi.localIP().toString().c_str());
    wifiWasConnected = wifiConnected;
  }
  if (!wifiConnected) {
    if (now - lastWifiAttempt >= kWifiRetryMs) {
      WiFi.reconnect();
      lastWifiAttempt = now;
    }
  } else if (due(now, nextContactAt)) {
    if (sequence >= 2147483647UL) {
      bootId = createBootId();
      sessionId = "";
      sequence = 0;
    }
    const bool ok = contactServer();
    if (!ok) Serial.println("Server unavailable or response rejected; motors remain stopped.");
    retryMs = ok ? kPollMs : min(retryMs * 2, kMaxRetryMs);
    nextContactAt = millis() + retryMs;
  }
  delay(1);
}
