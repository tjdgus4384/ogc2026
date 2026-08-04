// knobs.hpp -- every C++ tunable in ONE view, grouped by mechanism layer
// (causal map: docs/PIPELINE.md, laws L1-L6; provenance: the knob census).
// Each accessor caches its env read once (magic-static at first call) --
// identical semantics and cost to the former per-site lambdas, now with a
// single place to see, add, or retire a knob. -1 means "unset: use the
// call site's code default" for override-style knobs. All are inert on
// the grading server (no env set).
#pragma once
#include <cstdlib>

namespace knobs {
inline double envd(const char* n, double d) {
    const char* v = std::getenv(n); return v ? atof(v) : d;
}
inline int envi(const char* n, int d) {
    const char* v = std::getenv(n); return v ? atoi(v) : d;
}
// ------- construction layer (insertion order & screens; law L3)
inline double areaSlk() { static const double v = envd("OGC_AREASLK", 1.02); return v; }
inline double prefP()   { static const double v = envd("OGC_PREFP",  -1.0);  return v; }
inline int    stride()  { static const int    v = envi("OGC_STRIDE",  6);    return v; }
inline double cnoise()  { static const double v = envd("OGC_CNOISE",  3.0);  return v; }
// ------- destroy-mix layer (adaptive weights & personas; law L4)
inline double decay()   { static const double v = envd("OGC_DECAY",   0.85); return v; }
inline size_t op8Top()  { static const size_t v = (size_t)envi("OGC_OP8TOP", 4); return v; }
inline double hcFrac()  { static const double v = envd("OGC_HCFRAC", -1.0);  return v; }
inline double bigQ()    { static const double v = envd("OGC_BIGQ",   -1.0);  return v; }
// ------- guided construction (partial preference locks; python side)
inline double unlockF() { static const double v = envd("OGC_UNLOCKF", 0.6); return v; }
} // namespace knobs
