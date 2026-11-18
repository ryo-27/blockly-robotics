#pragma once

// Copy to robot_config.h (ignored by git) and configure each robot.
#define ROBOT_CONFIGURED false
// 実機試験の準備後にtrueへ変更する。未定義の既存設定はfalseとして扱う。
#define ROBOT_MOTION_ENABLED false
#define ROBOT_ID "robot-01"
#define ROBOT_TOKEN "replace-with-the-same-token-as-config-local-json"
#define WIFI_SSID "replace-with-L12-ssid"
#define WIFI_PASSWORD "replace-with-L12-password"
#define SERVER_URL "http://192.168.179.10:8000"
