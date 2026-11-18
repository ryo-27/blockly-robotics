#pragma once
#include <cstddef>
#include <cstdint>

namespace robot {
constexpr size_t kMaxCommands = 64;
constexpr uint32_t kMaxCommandMs = 5000;
constexpr uint32_t kMaxExecutionMs = 30000;
constexpr uint32_t kLeaseMs = 1500;

enum class Status { Idle, Running, Completed, Stopped, Error };
enum class Error { None, StopRequested, LinkLost, LeaseExpired, Timeout, InvalidProgram,
                   Replay, JournalFailure, MotionDisabled, SessionChanged };
struct Command { int8_t left = 0; int8_t right = 0; uint32_t durationMs = 0; };
struct Program {
  char id[37] = {};
  uint32_t sequence = 0;
  size_t count = 0;
  bool invertLeft = false;
  bool invertRight = true;
  Command commands[kMaxCommands] = {};
};
struct Snapshot {
  Status status = Status::Idle;
  Error error = Error::None;
  char executionId[37] = {};
  uint32_t executionSequence = 0;
  size_t commandIndex = 0;
};
class Output {
 public:
  virtual ~Output() = default;
  virtual void drive(int8_t left, int8_t right, bool invertLeft, bool invertRight) = 0;
  virtual void stop() = 0;
};
class Journal {
 public:
  virtual ~Journal() = default;
  virtual uint32_t highWater() const = 0;
  virtual bool consume(uint32_t sequence) = 0;
};
class Executor {
 public:
  Executor(Output& output, Journal& journal, bool motionEnabled);
  void start(const Program& program, uint32_t now, uint32_t leaseDeadline);
  void renew(const char* id, uint32_t sequence, uint32_t now, uint32_t leaseDeadline);
  void requestStop(const char* id, uint32_t sequence);
  void fault(Error error);
  void acknowledge(const char* id);
  void tick(uint32_t now);
  Snapshot snapshot() const { return state_; }
 private:
  Output& output_;
  Journal& journal_;
  bool motionEnabled_;
  Program program_{};
  Snapshot state_{};
  uint32_t startedAt_ = 0;
  uint32_t commandStartedAt_ = 0;
  uint32_t leaseDeadline_ = 0;
  void finish(Status status, Error error);
  void apply();
};
bool due(uint32_t now, uint32_t deadline);
const char* statusName(Status status);
const char* errorName(Error error);
}  // namespace robot
