#include <Arduino.h>
#include "motor_control.h"

namespace {
constexpr uint8_t kPins[] = {25, 26, 27, 14};
void setMotor(uint8_t first, uint8_t second, int8_t direction, bool inverted) {
  const int8_t corrected = inverted ? -direction : direction;
  digitalWrite(first, corrected > 0 ? HIGH : LOW);
  digitalWrite(second, corrected < 0 ? HIGH : LOW);
}
}
void stopMotors() {
  for (const auto pin : kPins) digitalWrite(pin, LOW);
}
void initializeMotors() {
  for (const auto pin : kPins) {
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT);
  }
  stopMotors();
}
void driveMotors(int8_t left, int8_t right, bool invertLeft, bool invertRight) {
  // 方向の切り替え時は両入力を落とし、反転補正はこの層だけで行う。
  stopMotors();
  setMotor(25, 26, left, invertLeft);
  setMotor(27, 14, right, invertRight);
}
