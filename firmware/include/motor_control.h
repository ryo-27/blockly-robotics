#pragma once
#include <cstdint>

void initializeMotors();
void stopMotors();
void driveMotors(int8_t left, int8_t right, bool invertLeft, bool invertRight);
