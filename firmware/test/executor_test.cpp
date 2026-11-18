#include "executor.h"
#include "control_message.h"
#include "program_parser.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <fstream>
#include <vector>

struct Motor : robot::Output {
  struct Entry { int left, right; bool invertLeft, invertRight; };
  std::vector<Entry> history;
  bool stopped = true;
  void drive(int8_t left, int8_t right, bool a, bool b) override {
    history.push_back({left, right, a, b}); stopped = !left && !right;
  }
  void stop() override { stopped = true; }
};
struct Journal : robot::Journal {
  uint32_t last = 0;
  bool writable = true;
  uint32_t highWater() const override { return last; }
  bool consume(uint32_t sequence) override {
    if (!writable) return false;
    last = sequence;
    return true;
  }
};
robot::Program program(uint32_t sequence = 1) {
  robot::Program p;
  std::strcpy(p.id, "898ce134-9a2c-46ef-813d-e30a96cd11fe");
  p.sequence = sequence;
  p.count = 3;
  p.commands[0] = {1, 1, 500};
  p.commands[1] = {1, -1, 330};
  p.commands[2] = {0, 0, 100};
  return p;
}
void executionAndLease() {
  Motor m; Journal j; robot::Executor e(m,j,true); auto p=program();
  e.start(p,0,1500); assert(!m.stopped && j.last==1);
  e.tick(499); assert(e.snapshot().commandIndex==0);
  e.tick(500); assert(e.snapshot().commandIndex==1 && m.history.back().right==-1);
  e.tick(830); assert(e.snapshot().commandIndex==2 && m.stopped);
  e.tick(930); assert(e.snapshot().status==robot::Status::Completed && m.stopped);
  e.start(program(2),940,2000); assert(j.last==1);
  e.acknowledge("wrong"); assert(e.snapshot().status==robot::Status::Completed);
  e.acknowledge(p.id); assert(e.snapshot().status==robot::Status::Idle);
  e.start(p,1000,2000); assert(e.snapshot().error==robot::Error::Replay && m.stopped);
}
void faultsAndRestart() {
  for(auto error:{robot::Error::LinkLost,robot::Error::StopRequested,robot::Error::SessionChanged}) {
    Motor m; Journal j; auto p=program(); robot::Executor e(m,j,true);
    e.start(p,0,1500); e.fault(error); assert(m.stopped);
    e.renew(p.id,1,300,1800); e.tick(400); assert(m.stopped && e.snapshot().status==robot::Status::Stopped);
    robot::Executor restarted(m,j,true); restarted.start(p,500,2000);
    assert(restarted.snapshot().error==robot::Error::Replay && m.stopped);
  }
  Motor m; Journal j; robot::Executor e(m,j,true); auto p=program();
  p.commands[0].durationMs=5000; e.start(p,0,1500);
  e.tick(1500); assert(e.snapshot().error==robot::Error::LeaseExpired && m.stopped);
  e.renew(p.id,1,1501,3000); assert(m.stopped);
}
void delayedAndWrappedTime() {
  Motor m; Journal j; robot::Executor e(m,j,true); auto p=program();
  e.start(p,1501,1500); assert(m.stopped && e.snapshot().error==robot::Error::LeaseExpired);
  e.acknowledge(p.id); p=program(2);
  const uint32_t start=0xffffff00UL;
  e.start(p,start,start+1500); e.tick(start+500); assert(e.snapshot().commandIndex==1);
  e.tick(start+830); e.tick(start+930); assert(e.snapshot().status==robot::Status::Completed);
  e.acknowledge(p.id); p=program(3); p.commands[0].durationMs=5000;
  e.start(p,0,1500); e.renew(p.id,3,1499,2500); e.tick(2499); assert(!m.stopped);
  e.tick(2500); assert(m.stopped);
}
void disabledStorageAndValidation() {
  Motor m; Journal j; robot::Executor disabled(m,j,false); auto p=program();
  disabled.start(p,0,1500); assert(m.stopped && j.last==0);
  j.writable=false; robot::Executor failed(m,j,true); failed.start(p,0,1500);
  assert(m.stopped && failed.snapshot().error==robot::Error::JournalFailure);
  j.writable=true;
  for(int bad=0;bad<4;++bad) {
    robot::Executor e(m,j,true); auto invalid=program();
    if(bad==0)invalid.count=0;
    if(bad==1)invalid.count=65;
    if(bad==2)invalid.commands[0].durationMs=5001;
    if(bad==3)invalid.commands[0].left=2;
    e.start(invalid,0,1500); assert(m.stopped && e.snapshot().status==robot::Status::Error);
  }
  robot::Executor stopped(m,j,true); stopped.requestStop(p.id,10);
  assert(m.stopped && j.last==10 && stopped.snapshot().status==robot::Status::Stopped);
}
void executionTimeLimit() {
  Motor m; Journal j; auto p=program(); p.count=60;
  for(size_t i=0;i<p.count;++i)p.commands[i]={1,1,400};
  robot::Executor e(m,j,true); e.start(p,0,1500);
  for(uint32_t now=1000;now<=30000;now+=1000) {
    e.renew(p.id,1,now,now+1500); e.tick(now);
  }
  assert(m.stopped && e.snapshot().error==robot::Error::Timeout);
}
JsonDocument jsonProgram() {
  JsonDocument d;
  d["executionId"]="898ce134-9a2c-46ef-813d-e30a96cd11fe";
  d["executionSequence"]=20;
  auto c=d["calibration"];
  c["invertLeft"]=false; c["invertRight"]=true;
  c["forwardOneCellMs"]=500; c["backwardOneCellMs"]=600;
  c["turnLeft90Ms"]=330; c["turnRight90Ms"]=340;
  const char* commands=R"([{"type":"MOVE","direction":"FORWARD","steps":2},{"type":"MOVE","direction":"BACKWARD","steps":1},{"type":"TURN","direction":"LEFT"},{"type":"TURN","direction":"RIGHT"},{"type":"WAIT","durationMs":100}])";
  JsonDocument a; assert(!deserializeJson(a,commands)); d["commands"]=a;
  return d;
}
void parser() {
  auto d=jsonProgram(); robot::Program p;
  assert(parseProgram(d.as<JsonVariantConst>(),p));
  assert(p.count==5 && p.invertRight && p.commands[0].durationMs==1000);
  assert(p.commands[1].left==-1 && p.commands[1].durationMs==600);
  assert(p.commands[2].left==-1 && p.commands[2].right==1 && p.commands[2].durationMs==330);
  assert(p.commands[3].left==1 && p.commands[3].right==-1 && p.commands[3].durationMs==340);
  assert(p.commands[4].left==0 && p.commands[4].durationMs==100);
  for(int bad=0;bad<9;++bad) {
    auto broken=jsonProgram();
    if(bad==0)broken["commands"][0]["steps"]=true;
    if(bad==1)broken["commands"][0]["steps"]=1.5;
    if(bad==2)broken["commands"][0]["steps"]=-1;
    if(bad==3)broken["commands"][0]["type"]="REPEAT";
    if(bad==4)broken["commands"][0]["extra"]=1;
    if(bad==5)broken["calibration"]["forwardOneCellMs"]=5000;
    if(bad==6)broken["executionId"]="invalid";
    if(bad==7)broken["commands"].clear();
    if(bad==8)broken["calibration"]["invertLeft"]=1;
    assert(!parseProgram(broken.as<JsonVariantConst>(),p));
  }
}
void serverContract(const char* path) {
  std::ifstream input(path);
  assert(input.good());
  JsonDocument response;
  assert(!deserializeJson(response,input));
  robot::Program p;
  assert(parseProgram(response.as<JsonVariantConst>(),p));
  assert(p.count==3 && p.commands[0].durationMs==1000 && p.commands[1].durationMs==330);
  Motor m; Journal j; robot::Executor e(m,j,true);
  e.start(p,0,1500);
  assert(!m.stopped && e.snapshot().status==robot::Status::Running);
}
void outgoingReports(const char* startPath) {
  const std::string directory=std::string(startPath).substr(0,std::string(startPath).find_last_of('/'));
  const char* id="898ce134-9a2c-46ef-813d-e30a96cd11fe";
  unsigned index=0;
  for(auto status:{robot::Status::Idle,robot::Status::Running,robot::Status::Completed,
                   robot::Status::Stopped,robot::Status::Error}) {
    robot::Snapshot state;
    state.status=status;
    if(status!=robot::Status::Idle)std::strcpy(state.executionId,id);
    if(status==robot::Status::Completed)state.commandIndex=3;
    if(status==robot::Status::Stopped)state.error=robot::Error::LinkLost;
    if(status==robot::Status::Error)state.error=robot::Error::MotionDisabled;
    JsonDocument request;
    buildControlRequest(request,id,id,++index,state);
    if(status!=robot::Status::Idle) {
      assert(!request["executionId"].as<JsonString>().isStatic());
      // 元のスナップショットが更新・破棄されても、送信内容は変わらない。
      std::memset(state.executionId,0,sizeof(state.executionId));
      assert(std::strcmp(request["executionId"].as<const char*>(),id)==0);
    } else assert(!request["executionId"].is<const char*>());
    std::ofstream output(directory+"/report-"+std::to_string(index)+".json");
    serializeJson(request,output);
  }
}
int main(int argc, char** argv) {
  if(argc==2){serverContract(argv[1]);outgoingReports(argv[1]);}
  executionAndLease(); faultsAndRestart(); delayedAndWrappedTime(); disabledStorageAndValidation(); executionTimeLimit(); parser();
  std::cout << "Firmware tests passed: sequencing, timeout, stop, replay, reboot, NVS failure, rollover and JSON validation\n";
}
