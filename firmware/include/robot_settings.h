#pragma once
#if __has_include("robot_config.h")
#include "robot_config.h"
#else
#include "robot_config.example.h"
#endif

#ifndef ROBOT_MOTION_ENABLED
#define ROBOT_MOTION_ENABLED false
#endif
