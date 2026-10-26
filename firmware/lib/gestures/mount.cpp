#include "mount.h"

namespace bp {

Vec3 axis_vector(MountAxis a) {
  switch (a) {
    case AXIS_POS_X: return {1, 0, 0};
    case AXIS_NEG_X: return {-1, 0, 0};
    case AXIS_POS_Y: return {0, 1, 0};
    case AXIS_NEG_Y: return {0, -1, 0};
    case AXIS_POS_Z: return {0, 0, 1};
    case AXIS_NEG_Z: return {0, 0, -1};
  }
  return {1, 0, 0};
}

void MountCalibration::start(uint32_t now_us) {
  active_ = true;
  start_us_ = now_us;
  acc_sum_ = {};
  gyro_sum_ = {};
  n_ = 0;
}

bool MountCalibration::feed(const Vec3& accel_g, const Vec3& gyro_dps, uint32_t t_us) {
  if (!active_) return done_;
  float an = accel_g.norm();
  bool still = an > 0.9f && an < 1.1f && gyro_dps.norm() < 20.0f;
  if (!still) {
    start(t_us);
    return false;
  }
  acc_sum_ += accel_g;
  gyro_sum_ += gyro_dps;
  ++n_;
  if (t_us - start_us_ < duration_us_ || n_ < 10) return false;
  set(acc_sum_ * (1.0f / n_), gyro_sum_ * (1.0f / n_));
  active_ = false;
  return true;
}

void MountCalibration::set(const Vec3& gravity_sensor, const Vec3& gyro_bias) {
  // Body axes expressed in the sensor frame: up = measured gravity reaction,
  // forward = the mount's forward axis with its vertical component removed.
  Vec3 up = gravity_sensor.normalized();
  Vec3 fwd = forward_ - up * forward_.dot(up);
  if (fwd.norm() < 0.3f) {
    // Forward axis nearly vertical: the mount setting is wrong; keep any horizontal axis.
    Vec3 alt = fabsf(up.x) < 0.9f ? Vec3{1, 0, 0} : Vec3{0, 1, 0};
    fwd = alt - up * alt.dot(up);
  }
  fwd = fwd.normalized();
  Vec3 left = up.cross(fwd);
  rot_ = Mat3::rows(fwd, left, up);  // body = rot * sensor
  gyro_bias_ = gyro_bias;
  done_ = true;
}

}  // namespace bp
