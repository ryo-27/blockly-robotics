#include "program_parser.h"
#include <cstring>

namespace {
bool integer(JsonVariantConst value, uint32_t low, uint32_t high, uint32_t& result) {
  if (!value.is<uint32_t>() || value.is<bool>()) return false;
  result = value.as<uint32_t>();
  return result >= low && result <= high;
}
bool uuid(const char* text) {
  if (!text || std::strlen(text) != 36) return false;
  for (size_t i = 0; i < 36; ++i) {
    if (i == 8 || i == 13 || i == 18 || i == 23) { if (text[i] != '-') return false; }
    else if (!((text[i] >= '0' && text[i] <= '9') || (text[i] >= 'a' && text[i] <= 'f'))) return false;
  }
  return true;
}
}
bool parseProgram(JsonVariantConst response, robot::Program& program) {
  const char* id = response["executionId"] | "";
  if (!uuid(id) || !integer(response["executionSequence"], 1, 2147483647, program.sequence)) return false;
  std::strcpy(program.id, id);
  const auto calibration = response["calibration"];
  if (!calibration["invertLeft"].is<bool>() || !calibration["invertRight"].is<bool>()) return false;
  program.invertLeft = calibration["invertLeft"];
  program.invertRight = calibration["invertRight"];
  uint32_t forward, backward, left, right;
  if (!integer(calibration["forwardOneCellMs"], 50, 5000, forward) ||
      !integer(calibration["backwardOneCellMs"], 50, 5000, backward) ||
      !integer(calibration["turnLeft90Ms"], 50, 5000, left) ||
      !integer(calibration["turnRight90Ms"], 50, 5000, right)) return false;
  const auto commands = response["commands"].as<JsonArrayConst>();
  if (commands.isNull() || !commands.size() || commands.size() > robot::kMaxCommands) return false;
  program.count = commands.size();
  uint32_t total = 0;
  size_t index = 0;
  for (const auto command : commands) {
    const char* type = command["type"] | "";
    const char* direction = command["direction"] | "";
    robot::Command result{};
    uint32_t steps;
    if (!std::strcmp(type, "MOVE")) {
      if (command.size() != 3 || !integer(command["steps"], 1, 10, steps)) return false;
      if (!std::strcmp(direction, "FORWARD")) { result = {1, 1, steps * forward}; }
      else if (!std::strcmp(direction, "BACKWARD")) { result = {-1, -1, steps * backward}; }
      else return false;
    } else if (!std::strcmp(type, "TURN")) {
      if (command.size() != 2) return false;
      if (!std::strcmp(direction, "LEFT")) { result = {-1, 1, left}; }
      else if (!std::strcmp(direction, "RIGHT")) { result = {1, -1, right}; }
      else return false;
    } else if (!std::strcmp(type, "WAIT")) {
      if (command.size() != 2 || !integer(command["durationMs"], 1, 5000, result.durationMs)) return false;
    } else return false;
    if (result.durationMs > robot::kMaxCommandMs) return false;
    total += result.durationMs;
    program.commands[index++] = result;
  }
  return total <= robot::kMaxExecutionMs;
}
