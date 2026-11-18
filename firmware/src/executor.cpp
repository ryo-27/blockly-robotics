#include "executor.h"
#include <cstring>

namespace robot {
bool due(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}
Executor::Executor(Output& output, Journal& journal, bool motionEnabled)
    : output_(output), journal_(journal), motionEnabled_(motionEnabled) {}

void Executor::finish(Status status, Error error) {
  output_.stop();
  state_.status = status;
  state_.error = error;
}
void Executor::apply() {
  const auto& command = program_.commands[state_.commandIndex];
  output_.drive(command.left, command.right, program_.invertLeft, program_.invertRight);
}
void Executor::start(const Program& program, uint32_t now, uint32_t leaseDeadline) {
  // 終端状態はサーバーのACKまで保持し、遅れて届いたSTARTで上書きしない。
  if (state_.status != Status::Idle) return;
  output_.stop();
  std::memcpy(state_.executionId, program.id, sizeof(state_.executionId));
  state_.executionId[36] = '\0';
  state_.executionSequence = program.sequence;
  if (!motionEnabled_) return finish(Status::Error, Error::MotionDisabled);
  uint32_t total = 0;
  if (!program.count || program.count > kMaxCommands || std::strlen(state_.executionId) != 36)
    return finish(Status::Error, Error::InvalidProgram);
  for (size_t i = 0; i < program.count; ++i) {
    const auto& command = program.commands[i];
    if (!command.durationMs || command.durationMs > kMaxCommandMs ||
        command.left < -1 || command.left > 1 || command.right < -1 || command.right > 1)
      return finish(Status::Error, Error::InvalidProgram);
    total += command.durationMs;
  }
  if (total > kMaxExecutionMs) return finish(Status::Error, Error::InvalidProgram);
  if (!program.sequence || program.sequence <= journal_.highWater())
    return finish(Status::Error, Error::Replay);
  // 出力開始より先に消費済み番号を保存し、電源断でも同じ実行を繰り返さない。
  if (!journal_.consume(program.sequence)) return finish(Status::Error, Error::JournalFailure);
  if (due(now, leaseDeadline)) return finish(Status::Stopped, Error::LeaseExpired);
  program_ = program;
  startedAt_ = commandStartedAt_ = now;
  leaseDeadline_ = leaseDeadline;
  state_.status = Status::Running;
  state_.commandIndex = 0;
  apply();
}
void Executor::renew(const char* id, uint32_t sequence, uint32_t now, uint32_t leaseDeadline) {
  if (state_.status != Status::Running) return;
  if (std::strcmp(id, state_.executionId) || sequence != state_.executionSequence)
    return fault(Error::SessionChanged);
  // 期限後に届いた応答で、失効済みの許可を復活させない。
  if (due(now, leaseDeadline_) || due(now, leaseDeadline)) return fault(Error::LeaseExpired);
  leaseDeadline_ = leaseDeadline;
}
void Executor::requestStop(const char* id, uint32_t sequence) {
  if (state_.status == Status::Idle && id[0]) {
    std::strncpy(state_.executionId, id, 36);
    state_.executionId[36] = '\0';
    state_.executionSequence = sequence;
    if (sequence > journal_.highWater() && !journal_.consume(sequence))
      return finish(Status::Error, Error::JournalFailure);
    return finish(Status::Stopped, Error::StopRequested);
  }
  fault(Error::StopRequested);
}
void Executor::fault(Error error) {
  output_.stop();
  if (state_.status == Status::Running) finish(Status::Stopped, error);
}
void Executor::acknowledge(const char* id) {
  if (state_.status == Status::Idle || state_.status == Status::Running ||
      std::strcmp(id, state_.executionId)) return;
  output_.stop();
  state_ = Snapshot{};
}
void Executor::tick(uint32_t now) {
  if (state_.status != Status::Running) return;
  if (due(now, leaseDeadline_)) return finish(Status::Stopped, Error::LeaseExpired);
  if (now - startedAt_ >= kMaxExecutionMs) return finish(Status::Stopped, Error::Timeout);
  if (now - commandStartedAt_ >= program_.commands[state_.commandIndex].durationMs) {
    output_.stop();
    ++state_.commandIndex;
    if (state_.commandIndex == program_.count) return finish(Status::Completed, Error::None);
    commandStartedAt_ = now;
    apply();
  }
}
const char* statusName(Status status) {
  switch (status) {
    case Status::Idle: return "IDLE";
    case Status::Running: return "RUNNING";
    case Status::Completed: return "COMPLETED";
    case Status::Stopped: return "STOPPED";
    case Status::Error: return "ERROR";
  }
  return "ERROR";
}
const char* errorName(Error error) {
  switch (error) {
    case Error::None: return "";
    case Error::StopRequested: return "STOP_REQUESTED";
    case Error::LinkLost: return "LINK_LOST";
    case Error::LeaseExpired: return "LEASE_EXPIRED";
    case Error::Timeout: return "EXECUTION_TIMEOUT";
    case Error::InvalidProgram: return "INVALID_PROGRAM";
    case Error::Replay: return "REPLAYED_EXECUTION";
    case Error::JournalFailure: return "JOURNAL_FAILURE";
    case Error::MotionDisabled: return "MOTION_DISABLED";
    case Error::SessionChanged: return "SESSION_CHANGED";
  }
  return "INVALID_PROGRAM";
}
}  // namespace robot
