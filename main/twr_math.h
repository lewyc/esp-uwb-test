#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#define TS_MASK UINT64_C(0xffffffffff)
#define UUS_TO_DTU UINT64_C(65536)
static inline uint64_t ts_delta(uint64_t a,uint64_t b) { return (a-b)&TS_MASK; }
/* t1 poll TX A; t2 poll RX B; t3 response TX B; t4 response RX A;
 * t5 final TX A; t6 final RX B. Differences remain integer until this point. */
static inline double twr_distance(const uint64_t t[6]) {
    uint64_t ra=ts_delta(t[3],t[0]), rb=ts_delta(t[5],t[2]);
    uint64_t da=ts_delta(t[4],t[3]), db=ts_delta(t[2],t[1]);
    if(!ra || !rb || !da || !db || ra>UINT64_C(6400000000) || rb>UINT64_C(6400000000) || da>UINT64_C(6400000000) || db>UINT64_C(6400000000)) return NAN;
    double tof=((double)ra*rb-(double)da*db)/((double)ra+rb+da+db);
    return tof*(1.0/(499.2e6*128.0))*299702547.0;
}

/* Synthetic exchange crossing the 40-bit timestamp wrap. The selected
 * round/reply intervals produce exactly 300 device-time units of flight. */
static inline bool twr_math_selftest(void) {
    const uint64_t t[6] = {
        TS_MASK - UINT64_C(499), TS_MASK - UINT64_C(99), UINT64_C(200),
        UINT64_C(500), UINT64_C(900), UINT64_C(1100)
    };
    const double expected = 300.0 * (1.0 / (499.2e6 * 128.0)) * 299702547.0;
    const double actual = twr_distance(t);
    return isfinite(actual) && fabs(actual - expected) < 1e-9;
}
