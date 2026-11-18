#pragma once
#include <ArduinoJson.h>
#include "executor.h"

bool parseProgram(JsonVariantConst response, robot::Program& program);
