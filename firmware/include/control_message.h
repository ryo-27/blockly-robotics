#pragma once
#include <ArduinoJson.h>
#include "executor.h"

void buildControlRequest(JsonDocument& request, const char* bootId, const char* sessionId,
                         uint32_t sequence, const robot::Snapshot& state);
