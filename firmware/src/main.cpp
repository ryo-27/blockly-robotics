#include <Arduino.h>
#include "motor_control.h"
#include "robot_runtime.h"
#include "robot_settings.h"

void setup() {
  initializeMotors();
  Serial.begin(115200);
  if (!ROBOT_CONFIGURED) {
    Serial.println("Configure robot_config.h first; motors remain stopped.");
    return;
  }
  startRobotRuntime();
}
void loop() {
  // GPIOは実行タスクだけが操作する。Arduinoのloopは走行を妨げない。
  delay(100);
}
