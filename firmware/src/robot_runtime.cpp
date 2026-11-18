#include <Arduino.h>
#include <atomic>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_system.h>
#include "executor.h"
#include "control_message.h"
#include "motor_control.h"
#include "program_parser.h"
#include "robot_settings.h"
#include "robot_runtime.h"

namespace {
constexpr char kFirmwareVersion[] = "0.2.1-phase2";
constexpr uint32_t kPollMs = 250;
constexpr uint32_t kWifiRetryMs = 10000;
constexpr uint32_t kMaxRetryMs = 10000;
enum class Action { Idle, Start, Continue, Stop, Ack };
struct Frame {
  Action action = Action::Idle;
  uint32_t generation = 0;
  uint32_t deadline = 0;
  robot::Program program{};
};
QueueHandle_t frames;
portMUX_TYPE stateMux = portMUX_INITIALIZER_UNLOCKED;
robot::Snapshot published{};
uint32_t faultGeneration = 0;
std::atomic<bool> tasksReady{false};
uint32_t generation();

class MotorOutput : public robot::Output {
 public:
  void authorize(uint32_t expectedGeneration, uint32_t deadline) {
    expectedGeneration_ = expectedGeneration;
    deadline_ = deadline;
  }
  void drive(int8_t left, int8_t right, bool invertLeft, bool invertRight) override {
    // NVS保存中に期限切れや通信失敗が起きても、出力を開始しない。
    if (generation() != expectedGeneration_ || robot::due(millis(), deadline_)) {
      stopMotors();
      return;
    }
    driveMotors(left, right, invertLeft, invertRight);
  }
  void stop() override { stopMotors(); }
 private:
  uint32_t expectedGeneration_ = 0;
  uint32_t deadline_ = 0;
};
class NvsJournal : public robot::Journal {
 public:
  bool begin() {
    ready_ = preferences_.begin("robot-executor", false);
    if (ready_) {
      highWater_ = preferences_.getULong("consumed", 0);
      Serial.printf("Consumed execution sequence: %lu\n", static_cast<unsigned long>(highWater_));
    }
    return ready_;
  }
  uint32_t highWater() const override { return highWater_; }
  bool consume(uint32_t sequence) override {
    if (!ready_ || sequence <= highWater_ || preferences_.putULong("consumed", sequence) != 4) return false;
    highWater_ = sequence;
    return true;
  }
 private:
  Preferences preferences_;
  uint32_t highWater_ = 0;
  bool ready_ = false;
};
void networkFault() {
  portENTER_CRITICAL(&stateMux);
  ++faultGeneration;
  portEXIT_CRITICAL(&stateMux);
}
uint32_t generation() {
  portENTER_CRITICAL(&stateMux);
  const auto result = faultGeneration;
  portEXIT_CRITICAL(&stateMux);
  return result;
}
robot::Snapshot snapshot() {
  portENTER_CRITICAL(&stateMux);
  const auto result = published;
  portEXIT_CRITICAL(&stateMux);
  return result;
}
void motorTask(void*) {
  MotorOutput output;
  NvsJournal journal;
  const bool storageReady = journal.begin();
  if (!storageReady) Serial.println("NVS initialization failed; motion cannot start.");
  robot::Executor executor(output, journal, ROBOT_MOTION_ENABLED);
  uint32_t seenGeneration = generation();
  Frame frame;
  for (;;) {
    const uint32_t current = generation();
    if (seenGeneration != current) {
      executor.fault(robot::Error::LinkLost);
      seenGeneration = current;
    }
    executor.tick(millis());
    while (xQueueReceive(frames, &frame, 0) == pdTRUE) {
      if (frame.generation != generation()) continue;
      const uint32_t now = millis();
      if (frame.action == Action::Start || frame.action == Action::Continue)
        output.authorize(frame.generation, frame.deadline);
      switch (frame.action) {
        case Action::Start: executor.start(frame.program, now, frame.deadline); break;
        case Action::Continue: executor.renew(frame.program.id, frame.program.sequence, now, frame.deadline); break;
        case Action::Stop: executor.requestStop(frame.program.id, frame.program.sequence); break;
        case Action::Ack: executor.acknowledge(frame.program.id); break;
        case Action::Idle: break;
      }
    }
    // フレームを処理する間に発生した通信失敗も、同じ周期で反映する。
    if (generation() != seenGeneration) executor.fault(robot::Error::LinkLost);
    executor.tick(millis());
    portENTER_CRITICAL(&stateMux);
    published = executor.snapshot();
    portEXIT_CRITICAL(&stateMux);
    vTaskDelay(pdMS_TO_TICKS(2));
  }
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
bool send(const String& path, const JsonDocument& request, JsonDocument& response, int& code) {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(750);
  http.setTimeout(750);
  String body;
  serializeJson(request, body);
  if (!http.begin(client, String(SERVER_URL) + path)) return false;
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + ROBOT_TOKEN);
  code = http.POST(body);
  bool valid = false;
  if (code == 200 && http.getSize() > 0 && http.getSize() <= 12288) {
    valid = !deserializeJson(response, http.getString());
  }
  if (!valid) Serial.printf("HTTP/JSON rejected on %s: %d; motors will stop.\n", path.c_str(), code);
  http.end();
  return valid;
}
bool readFrame(const JsonDocument& response, const String& session, uint32_t sequence,
               uint32_t requestStartedAt, uint32_t requestGeneration, Frame& frame) {
  if (!response["protocolVersion"].is<int>() || response["protocolVersion"].as<int>() != 2 ||
      response["sessionId"].as<String>() != session ||
      !response["sequence"].is<uint32_t>() || response["sequence"].as<uint32_t>() != sequence) return false;
  const String action = response["action"] | "";
  frame.generation = requestGeneration;
  if (action == "IDLE") frame.action = Action::Idle;
  else if (action == "START") {
    frame.action = Action::Start;
    if (!parseProgram(response.as<JsonVariantConst>(), frame.program)) return false;
  } else if (action == "CONTINUE") frame.action = Action::Continue;
  else if (action == "STOP") frame.action = Action::Stop;
  else if (action == "ACK") frame.action = Action::Ack;
  else return false;
  if (frame.action != Action::Idle && frame.action != Action::Start) {
    const String id = response["executionId"] | "";
    if (id.length() != 36) return false;
    id.toCharArray(frame.program.id, sizeof(frame.program.id));
    if (frame.action != Action::Ack) {
      if (!response["executionSequence"].is<uint32_t>()) return false;
      frame.program.sequence = response["executionSequence"];
    }
  }
  if (frame.action == Action::Start || frame.action == Action::Continue) {
    if (!response["leaseMs"].is<uint32_t>() || response["leaseMs"].as<uint32_t>() != robot::kLeaseMs) return false;
    // 受信時刻ではなく送信開始時刻から数え、遅延応答で許可を延長しない。
    frame.deadline = requestStartedAt + robot::kLeaseMs;
  } else if (!response["leaseMs"].is<uint32_t>() || response["leaseMs"].as<uint32_t>() != 0) return false;
  return true;
}
void networkTask(void*) {
  String bootId = createBootId(), session;
  uint32_t sequence = 0, nextContactAt = 0, retryMs = kPollMs;
  uint32_t wifiAttempt = millis();
  bool wifiWasConnected = false;
  Frame frame;
  for (;;) {
    if (!tasksReady) { vTaskDelay(pdMS_TO_TICKS(10)); continue; }
    const uint32_t now = millis();
    const bool connected = WiFi.status() == WL_CONNECTED;
    if (connected != wifiWasConnected) {
      Serial.println(connected ? "Wi-Fi connected." : "Wi-Fi disconnected; stopping.");
      if (connected) Serial.printf("Robot IP: %s\n", WiFi.localIP().toString().c_str());
      else networkFault();
      wifiWasConnected = connected;
    }
    if (!connected) {
      if (now - wifiAttempt >= kWifiRetryMs) { WiFi.reconnect(); wifiAttempt = now; }
    } else if (robot::due(now, nextContactAt)) {
      if (sequence >= 2147483647UL) { networkFault(); session = ""; bootId = createBootId(); }
      JsonDocument request, response;
      const bool registering = session.isEmpty();
      request["protocolVersion"] = registering ? 1 : 2;
      request["bootId"] = bootId;
      if (registering) {
        request["robotId"] = ROBOT_ID;
        request["firmwareVersion"] = kFirmwareVersion;
      } else {
        const auto state = snapshot();
        buildControlRequest(request, bootId.c_str(), session.c_str(), ++sequence, state);
      }
      int code = 0;
      const uint32_t requestStartedAt = millis(), requestGeneration = generation();
      bool ok = send(registering ? "/api/robot-agent/register" : "/api/robot-agent/control", request, response, code);
      if (ok && registering) {
        const String received = response["sessionId"] | "";
        ok = response["protocolVersion"].as<int>() == 1 && received.length() == 36 &&
             response["motorsEnabled"].is<bool>() && !response["motorsEnabled"].as<bool>();
        if (ok) { session = received; sequence = 0; Serial.println("Server registration accepted; motors remain stopped until a new test execution."); }
      } else if (ok) {
        frame = Frame{};
        ok = readFrame(response, session, sequence, requestStartedAt, requestGeneration, frame) &&
             xQueueSend(frames, &frame, 0) == pdTRUE;
      }
      if (!ok) {
        networkFault();
        if (code == 401 || code == 403 || code == 409) { session = ""; sequence = 0; }
      }
      retryMs = ok ? kPollMs : min(retryMs * 2, kMaxRetryMs);
      nextContactAt = millis() + retryMs;
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}
}
void startRobotRuntime() {
  frames = xQueueCreate(4, sizeof(Frame));
  if (!frames) { stopMotors(); Serial.println("Queue allocation failed; stopped."); return; }
  TaskHandle_t motorHandle = nullptr, networkHandle = nullptr;
  if (xTaskCreatePinnedToCore(motorTask, "motor-executor", 8192, nullptr, 3, &motorHandle, 1) != pdPASS) {
    stopMotors(); Serial.println("Motor task failed; stopped."); return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  if (xTaskCreatePinnedToCore(networkTask, "robot-network", 16384, nullptr, 1, &networkHandle, 0) != pdPASS) {
    networkFault(); Serial.println("Network task failed; stopped."); return;
  }
  tasksReady = true;
}
