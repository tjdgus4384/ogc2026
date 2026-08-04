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
inline int    slackOn() { static const int v = envi("OGC_SLACK", 0);
                         return v; }
inline int    ota()     { static const int v = envi("OGC_OTA", 0);
                         return v; }
inline int    qhiMul() { static const int v = envi("OGC_QHIMUL", 100);
                        return v; }
inline int    shiftSplit() { static const int v = envi("OGC_SHIFTSPLIT", 0);
                            return v; }
inline int    exactCut() { static const int v = envi("OGC_EXACTCUT", 0);
                          return v; }
inline int    rescueB() { static const int v = envi("OGC_RESCUEB", 1);
                         return v; }
inline int    rescueP() { static const int v = envi("OGC_RESCUE", 0);
                         return v; }
inline int    iCompact() { static const int v = envi("OGC_ICOMPACT", 0);
                          return v; }
inline int    compactY() { static const int v = envi("OGC_COMPACTY", 0);
                           return v; }
inline int    nfpAuto() { static const int v = envi("OGC_NFPAUTO", 0);
                          return v; }
inline int    costMix() { static const int v = envi("OGC_COSTMIX", 0);
                          return v; }
inline int    congest() { static const int v = envi("OGC_CONGEST", 0);
                          return v; }
inline int    relink()  { static const int v = envi("OGC_RELINK", 0);
                         return v; }
inline int    tabuP()   { static const int v = envi("OGC_TABUP", 20);
                         return v; }
inline int    squeak()  { static const int v = envi("OGC_SQUEAK", 0);
                         return v; }
inline int    tabu()    { static const int v = envi("OGC_TABU", 0);
                         return v; }
inline int    shiftLoc() { static const int v = envi("OGC_SHIFTLOC", 0);
                          return v; }
inline int    shiftP()  { static const int v = envi("OGC_SHIFTP", 100);
                         return v; }
inline int    reorP()   { static const int v = envi("OGC_REORP", 0);
                         return v; }
inline int    swapRel() { static const int v = envi("OGC_SWAPREL", 0);
                          return v; }
inline int    chainMax() { static const int v = envi("OGC_CHAINMAX", 1);
                          return v; }
inline int    takeover() { static const int v = envi("OGC_TAKEOVER", 0);
                          return v; }
inline int    swap3()   { static const int v = envi("OGC_SWAP3", 0);
                         return v; }
inline int    swapSel() { static const int v = envi("OGC_SWAPSEL", 0);
                          return v; }
inline int    swapP()   { static const int v = envi("OGC_SWAPP", 20);
                         return v; }
inline int    scaleSched() { static const int v =
    envi("OGC_SCALESCHED", 1); return v; }
inline int    retN()    { static const int v = envi("OGC_RETURN", 20);
                         return v; }
inline int    qFrac()   { static const int v = envi("OGC_QFRAC", 3);
                         return v; }
inline int    reserve()  { static const int v = envi("OGC_RESERVE", 1);
                          return v; }
inline int    bottleneck() { static const int v =
    envi("OGC_BOTTLENECK", 1); return v; }
inline int    ejectMul() { static const int v = envi("OGC_EJECTMUL", 1);
                          return v; }
inline int    ejectAll() { static const int v = envi("OGC_EJECTALL", 1);
                          return v; }
inline int    mixDens() { static const int v = envi("OGC_MIXDENS", 1);
                         return v; }
inline int    polyArea() { static const int v = envi("OGC_POLYAREA", 1);
                          return v; }
inline int    oriPref() { static const int v = envi("OGC_ORIPREF", 50);
                         return v; }
inline int    mixAcc()  { static const int v = envi("OGC_MIXACC", 0);
                         return v; }
inline int    rOrder()  { static const int v = envi("OGC_RORDER", 2);
                         return v; }
inline int    lahcMul() { static const int v = envi("OGC_LAHCMUL", 4);
                         return v; }
inline int    nfp2()    { static const int v = envi("OGC_NFP2", 0);
                         return v; }
inline int    repairR() { static const int v = envi("OGC_REPAIRR", 1);
                         return v; }
inline int    lahc()    { static const int v = envi("OGC_LAHC", 0);
                         return v; }
inline int    sCompact(){ static const int v = envi("OGC_SCOMPACT", 6);
                         return v; }
inline int    nfp()     { static const int v = envi("OGC_NFP", 0);
                         return v; }
inline int    nest()    { static const int v = envi("OGC_NEST", 0);
                         return v; }
inline double slackDef() { static const double v = [] {
    const char* q = getenv("OGC_SLACKDEF"); return q ? atof(q) : 0.0;
    }(); return v; }
inline int    bestFit() { static const int v = envi("OGC_BESTFIT", 0);
                         return v; }
inline double gls()     { static const double v = [] {
    const char* q = getenv("OGC_GLS"); return q ? atof(q) : 0.0; }();
    return v; }
inline int    slide()   { static const int    v = envi("OGC_SLIDE",   0);   return v; }
// slide default OFF: contact-tight fits replace "defer entry" with
// "cram in" -- rounds=1 (TL>110) cannot re-anneal out of the
// interlock (P3 TL240 repro: obj1 139,692). The raster-NFP leap
// lives in the CP vocabulary track (XGEN4) instead.
} // namespace knobs
