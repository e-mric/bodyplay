// Minimal 3-vector / 3x3 matrix math for gesture code (no heap, no Arduino).
#pragma once

#include <math.h>

namespace bp {

struct Vec3 {
  float x = 0, y = 0, z = 0;
  constexpr Vec3() = default;
  constexpr Vec3(float x_, float y_, float z_) : x(x_), y(y_), z(z_) {}
  Vec3 operator+(const Vec3& o) const { return {x + o.x, y + o.y, z + o.z}; }
  Vec3 operator-(const Vec3& o) const { return {x - o.x, y - o.y, z - o.z}; }
  Vec3 operator*(float s) const { return {x * s, y * s, z * s}; }
  Vec3& operator+=(const Vec3& o) {
    x += o.x;
    y += o.y;
    z += o.z;
    return *this;
  }
  float dot(const Vec3& o) const { return x * o.x + y * o.y + z * o.z; }
  Vec3 cross(const Vec3& o) const { return {y * o.z - z * o.y, z * o.x - x * o.z, x * o.y - y * o.x}; }
  float norm() const { return sqrtf(dot(*this)); }
  Vec3 normalized() const {
    float n = norm();
    return n > 1e-9f ? *this * (1.0f / n) : Vec3{0, 0, 0};
  }
};

struct Mat3 {
  // Row-major: out = m * v.
  float m[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};

  Vec3 operator*(const Vec3& v) const {
    return {m[0][0] * v.x + m[0][1] * v.y + m[0][2] * v.z, m[1][0] * v.x + m[1][1] * v.y + m[1][2] * v.z,
            m[2][0] * v.x + m[2][1] * v.y + m[2][2] * v.z};
  }
  Mat3 operator*(const Mat3& o) const {
    Mat3 r;
    for (int i = 0; i < 3; ++i)
      for (int j = 0; j < 3; ++j) r.m[i][j] = m[i][0] * o.m[0][j] + m[i][1] * o.m[1][j] + m[i][2] * o.m[2][j];
    return r;
  }
  static Mat3 rows(const Vec3& a, const Vec3& b, const Vec3& c) {
    Mat3 r;
    const Vec3 v[3] = {a, b, c};
    for (int i = 0; i < 3; ++i) {
      r.m[i][0] = v[i].x;
      r.m[i][1] = v[i].y;
      r.m[i][2] = v[i].z;
    }
    return r;
  }
};

}  // namespace bp
