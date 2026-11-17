#include <Arduino.h>
#include "motor_control.h"

namespace {
constexpr uint8_t kPins[] = {25, 26, 27, 14};
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
