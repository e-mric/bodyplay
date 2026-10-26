// Sensor -> body frame. The node is strapped on with some known board axis
// pointing forward (profile setting), then a 2 s standing calibration measures
// gravity and tilts the frame so +z is exactly up. Body frame: x forward,
// y left, z up; the accelerometer reads +1 g on z when standing still.
#pragma once

#include <stdint.h>

#include "vec3.h"

namespace bp {

enum MountAxis : uint8_t { AXIS_POS_X = 0, AXIS_NEG_X, AXIS_POS_Y, AXIS_NEG_Y, AXIS_POS_Z, AXIS_NEG_Z };

Vec3 axis_vector(MountAxis a);

class MountCalibration {
 public:
  // forward: the board axis that points forward when worn.
  explicit MountCalibration(MountAxis forward = AXIS_POS_X, uint32_t duration_us = 2000000)
      : forward_(axis_vector(forward)), duration_us_(duration_us) {}

  void start(uint32_t now_us);
  // Feed raw samples (sensor frame, g and dps) while calibrating. Returns true
  // when calibration completes; false while still collecting. A sample that
  // isn't "standing still" restarts the window.
  bool feed(const Vec3& accel_g, const Vec3& gyro_dps, uint32_t t_us);

  bool done() const { return done_; }
  bool active() const { return active_; }
  Vec3 gyro_bias() const { return gyro_bias_; }

  Vec3 to_body(const Vec3& sensor) const { return rot_ * sensor; }
  Vec3 gyro_to_body(const Vec3& sensor_dps) const { return rot_ * (sensor_dps - gyro_bias_); }

  // Directly set gravity (sensor frame) and bias, e.g. restored from NVS.
  void set(const Vec3& gravity_sensor, const Vec3& gyro_bias);

 private:
  Vec3 forward_;
  uint32_t duration_us_;
  bool active_ = false;
  bool done_ = false;
  uint32_t start_us_ = 0;
  Vec3 acc_sum_;
  Vec3 gyro_sum_;
  uint32_t n_ = 0;
  Mat3 rot_;
  Vec3 gyro_bias_;
};

}  // namespace bp
