#include "control_message.h"

void buildControlRequest(JsonDocument& request, const char* bootId, const char* sessionId,
                         uint32_t sequence, const robot::Snapshot& state) {
  request.clear();
  request["protocolVersion"] = 2;
  request["bootId"] = JsonString(bootId);
  request["sessionId"] = JsonString(sessionId);
  request["sequence"] = sequence;
  request["status"] = robot::statusName(state.status);
  request["currentCommandIndex"] = state.commandIndex;
  // const char配列はリテラルと同じ扱いになるため、寿命に依存しないコピーを明示する。
  if (state.executionId[0]) request["executionId"] = JsonString(state.executionId);
  if (state.error != robot::Error::None) request["errorCode"] = robot::errorName(state.error);
}
