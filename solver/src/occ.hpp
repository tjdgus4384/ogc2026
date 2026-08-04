// occ.hpp -- bit-packed occupancy grid per (bay, layer, time) with
// under-approximate block masks (cell strictly inside the polygon).
//
// Mask AND-hit => both polygons strictly contain that cell centre => certain
// positive-area SAME-LAYER overlap => the placement is certainly infeasible
// (checker rule 1). So the prefilter's rejections are always sound; anything
// it admits still goes through vpConservative. One prefilter, one semantic.
#pragma once
#include "knobs.hpp"
#include "core.hpp"
#include <vector>
#include <cstdint>
#include <climits>
#include <cstdio>
#include <cstdlib>

namespace occ {
using core::Problem; using core::Sol;

struct Mask {                       // one (block, orient, layer)
    int w = 0, h = 0;               // cells
    int ox = 0, oy = 0;             // offset of mask cell (0,0) in block-local ints
    std::vector<uint64_t> rows;     // h rows, each ceil(w/64) words
    int wpr = 0;                    // words per row
    bool any = false;
};

inline Mask rasterize(const geom::Poly& poly) {
    Mask m;
    auto bb = geom::bbox(poly);
    int x0 = (int)std::floor(bb.x0), y0 = (int)std::floor(bb.y0);
    int x1 = (int)std::ceil(bb.x1),  y1 = (int)std::ceil(bb.y1);
    m.w = std::max(0, x1 - x0); m.h = std::max(0, y1 - y0);
    m.ox = x0; m.oy = y0;
    m.wpr = (m.w + 63) / 64;
    m.rows.assign((size_t)m.h * m.wpr, 0);
    for (int cy = 0; cy < m.h; cy++) {
        for (int cx = 0; cx < m.w; cx++) {
            geom::Pt c{x0 + cx + 0.5, y0 + cy + 0.5};
            if (geom::strictlyInside(poly, c)) {
                m.rows[(size_t)cy * m.wpr + (cx >> 6)] |= (1ULL << (cx & 63));
                m.any = true;
            }
        }
    }
    return m;
}

struct Grid {
    int W = 0, H = 0, T = 0, L = 0, wpr = 0;
    std::vector<uint64_t> bits;     // [l][t][y][word]
    inline uint64_t* row(int l, int t, int y) {
        return &bits[(((size_t)l * T + t) * H + y) * wpr];
    }
    void init(int W_, int H_, int T_, int L_) {
        W = W_; H = H_; T = T_; L = L_;
        wpr = (W + 63) / 64;
        bits.assign((size_t)L * T * H * wpr, 0);
    }
};

struct OccIndex {
    std::vector<Grid> bay;                       // per bay
    std::vector<std::vector<double>> areaUsed;   // [bay][t] bbox-area sum
    std::vector<std::vector<Mask>> masks;        // [block][orient*Lmax + l]
    std::vector<int> nOri, nLay;                 // per block
    int Lmax = 0, T = 0;

    void build(const Problem& pb, int horizon) {
        T = horizon;
        int n = pb.n;
        nOri.resize(n); nLay.resize(n);
        Lmax = 0;
        for (int i = 0; i < n; i++) {
            nOri[i] = (int)pb.geo[i].orients.size();
            int L = 0;
            for (auto& o : pb.geo[i].orients) L = std::max(L, (int)o.layers.size());
            nLay[i] = L; Lmax = std::max(Lmax, L);
        }
        bay.resize(pb.m);
        for (int j = 0; j < pb.m; j++)
            bay[j].init((int)std::ceil(pb.bayW[j]), (int)std::ceil(pb.bayH[j]),
                        T, Lmax);
        areaUsed.assign(pb.m, std::vector<double>(T, 0.0));
        masks.resize(n);
        for (int i = 0; i < n; i++) {
            masks[i].resize((size_t)nOri[i] * Lmax);
            for (int oi = 0; oi < nOri[i]; oi++) {
                auto& lay = pb.geo[i].orients[oi].layers;
                for (int l = 0; l < (int)lay.size(); l++)
                    masks[i][(size_t)oi * Lmax + l] = rasterize(lay[l]);
            }
        }
    }

    // time-collapsed window snapshot: OR of the grid over t in [e,f), per
    // layer. hitsSnap(sn,...) == "exists t in [e,f) with a same-layer AND-hit"
    // -- the time loop is paid ONCE per (bay,e,f) instead of per candidate.
    struct Snap {
        int H = 0, wpr = 0, L = 0;
        std::vector<uint64_t> rows;                // [l][y][w]
        const uint64_t* row(int l, int y) const {
            return &rows[((size_t)l * H + y) * wpr];
        }
    };
    void buildSnap(int jT, int e, int f, Snap& sn) {
        Grid& g = bay[jT];
        sn.H = g.H; sn.wpr = g.wpr; sn.L = g.L;
        sn.rows.assign((size_t)g.L * g.H * g.wpr, 0);
        int fT = std::min(f, g.T);
        for (int l = 0; l < g.L; l++) {
            uint64_t* dst = &sn.rows[(size_t)l * g.H * g.wpr];
            for (int t = (e < 0 ? 0 : e); t < fT; t++) {
                const uint64_t* src = g.row(l, t, 0);
                size_t nw = (size_t)g.H * g.wpr;
                for (size_t wq = 0; wq < nw; wq++) dst[wq] |= src[wq];
            }
        }
    }
    // raster-NFP slide: leftmost placement x in [xlo, xhi] where the
    // full polygon mask fits at row y -- generates POLYGON-CONTACT
    // positions the corner scan never produces (the measured generator
    // bottleneck behind the P3 nine-proof ceiling).
    // bit-parallel: forbidden-x sets computed by dilating snapshot rows
    // (OR of doubling right-shifts) per mask-row run, so the FULL free-x
    // set at a row costs about one probe -- slide can run at every row.
    static void shiftOr(uint64_t* T, const uint64_t* S, int nw, int d) {
        // T |= S >> d (d may be negative = left shift), multiword
        if (d >= 0) {
            int q = d >> 6, r = d & 63;
            for (int k = 0; k < nw; k++) {
                uint64_t v = 0;
                if (k + q < nw) v = S[k + q] >> r;
                if (r && k + q + 1 < nw) v |= S[k + q + 1] << (64 - r);
                T[k] |= v;
            }
        } else {
            int q = (-d) >> 6, r = (-d) & 63;
            for (int k = nw - 1; k >= 0; k--) {
                uint64_t v = 0;
                if (k - q >= 0) v = S[k - q] << r;
                if (r && k - q - 1 >= 0) v |= S[k - q - 1] >> (64 - r);
                T[k] |= v;
            }
        }
    }
    // fastscan: forbidden-x bitset for row y over [0, xhi], caller
    // buffer FB (>= 20 words, zeroed by callee). Same dilation core as
    // slideLeftX (0-mismatch certified vs hitsSnap). Returns working
    // word count, or -1 when the row must fall back to hitsSnap.
    int freeRowFB(const Snap& sn, int i, int oi, int y, int xhi,
                  uint64_t* FB) {
        int nw = sn.wpr;
        int nwx = (xhi >> 6) + 1;
        if (nwx < nw) nwx = nw;
        if (nwx > 20) return -1;
        for (int w = 0; w < nwx; w++) FB[w] = 0;
        uint64_t D[20], E[20], SS[20];
        for (int l = 0; l < nLay[i]; l++) {
            const Mask& mk = masks[i][(size_t)oi * Lmax + l];
            if (!mk.any) continue;
            for (int my = 0; my < mk.h; my++) {
                int gy = y + mk.oy + my;
                if (gy < 0 || gy >= sn.H) continue;
                const uint64_t* S = sn.row(l, gy);
                for (int w = 0; w < nwx; w++)
                    SS[w] = (w < nw) ? S[w] : 0;
                const uint64_t* src = &mk.rows[(size_t)my * mk.wpr];
                int cx = 0;
                while (cx < mk.w) {
                    int a = -1;
                    { int wd = cx >> 6, bo = cx & 63;
                      uint64_t v = src[wd] >> bo;
                      while (true) {
                          if (v) { a = (wd << 6) + bo +
                                       __builtin_ctzll(v); break; }
                          if (++wd >= mk.wpr) break;
                          v = src[wd]; bo = 0; } }
                    if (a < 0 || a >= mk.w) break;
                    int b;
                    { int wd = a >> 6, bo = a & 63;
                      uint64_t v = (~src[wd]) >> bo;
                      while (true) {
                          if (v) { b = (wd << 6) + bo +
                                       __builtin_ctzll(v) - 1; break; }
                          if (++wd >= mk.wpr) { b = mk.wpr*64 - 1;
                                                break; }
                          v = ~src[wd]; bo = 0; }
                      if (b >= mk.w) b = mk.w - 1; }
                    int lo = mk.ox + a, hi = mk.ox + b;
                    auto dil = [&](int base, int k, int dir) {
                        for (int w = 0; w < nwx; w++) D[w] = 0;
                        shiftOr(D, SS, nwx, dir * base);
                        for (int s = 1; s < k; s <<= 1) {
                            int step = std::min(s, k - s);
                            for (int w = 0; w < nwx; w++) E[w] = D[w];
                            shiftOr(D, E, nwx, dir * step);
                        }
                        for (int w = 0; w < nwx; w++) FB[w] |= D[w];
                    };
                    if (lo >= 0) dil(lo, hi - lo + 1, +1);
                    else if (hi < 0) dil(-hi, hi - lo + 1, -1);
                    else { dil(0, hi + 1, +1); dil(1, -lo, -1); }
                    cx = b + 2;
                }
            }
        }
        return nwx;
    }
    // TRUE NFP CANDIDATE SET (both axes). Build the forbidden-x
    // bitset for EVERY row once, then read it two ways: run
    // boundaries along a row are x-contacts, run boundaries down a
    // column are y-contacts. A position that is a boundary in BOTH is
    // exactly a vertex of the discrete no-fit-polygon arrangement --
    // the piece touches geometry horizontally AND vertically. The
    // engine's own generator only ever produced resident BOUNDING BOX
    // edges, so this is the first time the search sees real polygon
    // contacts on both axes. Returns pairs written to outx/outy.
    int nfpCands(const Snap& sn, int i, int oi, int xlo, int xhi,
                 int ylo, int yhi, int* outx, int* outy, int cap) {
        if (xhi < xlo || yhi < ylo || cap <= 0) return 0;
        int nrow = yhi - ylo + 1;
        if (nrow > 96) nrow = 96;
        static thread_local std::vector<uint64_t> rows;
        int nwx = ((xhi) >> 6) + 1;
        if (nwx < sn.wpr) nwx = sn.wpr;
        if (nwx > 20) return 0;
        rows.assign((size_t)nrow * nwx, ~0ULL);
        for (int r = 0; r < nrow; r++) {
            uint64_t FB[20];
            int got = freeRowFB(sn, i, oi, ylo + r, xhi, FB);
            if (got < 0) continue;
            for (int w = 0; w < nwx; w++)
                rows[(size_t)r * nwx + w] = (w < got) ? FB[w] : ~0ULL;
        }
        auto blocked = [&](int r, int x) {
            return (rows[(size_t)r * nwx + (x >> 6)] >> (x & 63)) & 1ULL;
        };
        int cnt = 0;
        for (int r = 0; r < nrow; r++) {
            int x = xlo;
            while (x <= xhi) {
                if (blocked(r, x)) { x++; continue; }
                int a = x;
                while (x + 1 <= xhi && !blocked(r, x + 1)) x++;
                for (int cx : {a, x}) {           // x-contact ends
                    // y-contact test at this column: is r a run end?
                    bool topEnd = (r == 0) || blocked(r - 1, cx);
                    bool botEnd = (r == nrow - 1) || blocked(r + 1, cx);
                    if (topEnd || botEnd) {
                        outx[cnt] = cx; outy[cnt] = ylo + r;
                        if (++cnt >= cap) return cnt;
                    }
                    if (a == x) break;
                }
                x++;
            }
        }
        return cnt;
    }

    // POLYGON-CONTACT CANDIDATES (NFP-class, raster form): every
    // maximal free run of x at row y is bounded by positions where
    // the moving polygon TOUCHES geometry -- its left end abuts the
    // blocker on the left, its right end abuts the one on the right.
    // Those run ends are exactly the discrete no-fit-polygon contact
    // set for this row, and they are what the bbox-corner generator
    // can never produce (measured: bbox utilisation 103-105% while
    // polygon utilisation is 58-65%, i.e. the engine packs boxes).
    // Returns how many were written to out.
    int contactXs(const Snap& sn, int i, int oi, int y, int xlo,
                  int xhi, int* out, int cap) {
        if (xhi < xlo || cap <= 0) return 0;
        uint64_t FB[20];
        int nwx = freeRowFB(sn, i, oi, y, xhi, FB);
        if (nwx < 0) return 0;
        int cnt = 0, x = xlo;
        while (x <= xhi) {
            if ((FB[x >> 6] >> (x & 63)) & 1ULL) { x++; continue; }
            int a = x;
            while (x + 1 <= xhi &&
                   !((FB[(x + 1) >> 6] >> ((x + 1) & 63)) & 1ULL))
                x++;
            out[cnt++] = a;                       // left contact
            if (cnt >= cap) return cnt;
            if (x != a) { out[cnt++] = x;         // right contact
                          if (cnt >= cap) return cnt; }
            x++;
        }
        return cnt;
    }
    int slideLeftX(const Snap& sn, int i, int oi, int y, int xlo,
                   int xhi) {
        if (xhi < xlo) return INT_MIN;
        // anchors can sit left of the polygon (xlo < 0) or past the
        // bay-width register (xhi >= wpr*64) -- work in a frame
        // shifted by x0 with a zero-padded register wide enough for
        // the whole candidate range (both were unit-tested
        // false-free bugs).
        int x0 = xlo < 0 ? xlo : 0;
        int nwx = ((xhi - x0) >> 6) + 1;
        int nw = sn.wpr;
        if (nwx < nw) nwx = nw;    // source words must survive: right
                                   // shifts pull high snap bits down
        int res = INT_MIN;
        if (nwx > 20) {
            for (int x = xlo; x <= xhi; x++)
                if (!hitsSnap(sn, i, oi, x, y)) { res = x; break; }
            return res;
        }
        uint64_t FB[20] = {0}, D[20], E[20], SS[20];
        for (int l = 0; l < nLay[i]; l++) {
            const Mask& mk = masks[i][(size_t)oi * Lmax + l];
            if (!mk.any) continue;
            int oxE = mk.ox + x0;
            for (int my = 0; my < mk.h; my++) {
                int gy = y + mk.oy + my;
                if (gy < 0 || gy >= sn.H) continue;
                const uint64_t* S = sn.row(l, gy);
                for (int w = 0; w < nwx; w++)
                    SS[w] = (w < nw) ? S[w] : 0;
                const uint64_t* src = &mk.rows[(size_t)my * mk.wpr];
                // walk runs [a,b] of this mask row (ctz word-level)
                int cx = 0;
                while (cx < mk.w) {
                    int a = -1;
                    { int wd = cx >> 6, bo = cx & 63;
                      uint64_t v = src[wd] >> bo;
                      while (true) {
                          if (v) { a = (wd << 6) + bo +
                                       __builtin_ctzll(v); break; }
                          if (++wd >= mk.wpr) break;
                          v = src[wd]; bo = 0; } }
                    if (a < 0 || a >= mk.w) break;
                    int b;
                    { int wd = a >> 6, bo = a & 63;
                      uint64_t v = (~src[wd]) >> bo;
                      while (true) {
                          if (v) { b = (wd << 6) + bo +
                                       __builtin_ctzll(v) - 1; break; }
                          if (++wd >= mk.wpr) { b = mk.wpr*64 - 1;
                                                break; }
                          v = ~src[wd]; bo = 0; }
                      if (b >= mk.w) b = mk.w - 1; }
                    // one-directional dilation chains only: mixing
                    // left and right shifts drops bits at register
                    // edges mid-chain.
                    int lo = oxE + a, hi = oxE + b;
                    auto dil = [&](int base, int k, int dir) {
                        for (int w = 0; w < nwx; w++) D[w] = 0;
                        shiftOr(D, SS, nwx, dir * base);
                        for (int s = 1; s < k; s <<= 1) {
                            int step = std::min(s, k - s);
                            for (int w = 0; w < nwx; w++) E[w] = D[w];
                            shiftOr(D, E, nwx, dir * step);
                        }
                        for (int w = 0; w < nwx; w++) FB[w] |= D[w];
                    };
                    if (lo >= 0) dil(lo, hi - lo + 1, +1);
                    else if (hi < 0) dil(-hi, hi - lo + 1, -1);
                    else { dil(0, hi + 1, +1); dil(1, -lo, -1); }
                    cx = b + 2;
                }
            }
        }
        for (int x = xlo; x <= xhi; x++) {
            int xp = x - x0;
            if (!((FB[xp >> 6] >> (xp & 63)) & 1ULL)) { res = x; break; }
        }
        static const bool chk = getenv("OGC_SLIDECHECK") != nullptr;
        if (chk) {
            int lin = INT_MIN;
            for (int x = xlo; x <= xhi; x++)
                if (!hitsSnap(sn, i, oi, x, y)) { lin = x; break; }
            if (lin != res)
                fprintf(stderr, "SLIDEMISMATCH i=%d oi=%d y=%d "
                        "bit=%d lin=%d xlo=%d xhi=%d nw=%d\n",
                        i, oi, y, res, lin, xlo, xhi, nw);
        }
        return res;
    }
    bool hitsSnap(const Snap& sn, int i, int oi, int x, int y) {
        for (int l = 0; l < nLay[i]; l++) {
            const Mask& mk = masks[i][(size_t)oi * Lmax + l];
            if (!mk.any) continue;
            int bx = x + mk.ox, by = y + mk.oy;
            int sh = bx & 63, w0 = bx >> 6;
            for (int my = 0; my < mk.h; my++) {
                int gy = by + my;
                if (gy < 0 || gy >= sn.H) continue;
                const uint64_t* dst = sn.row(l, gy);
                const uint64_t* src = &mk.rows[(size_t)my * mk.wpr];
                for (int wd = 0; wd < mk.wpr; wd++) {
                    uint64_t v = src[wd];
                    if (!v) continue;
                    if (dst[w0 + wd] & (v << sh)) return true;
                    if (sh && w0 + wd + 1 < sn.wpr &&
                        (dst[w0+wd+1] & (v >> (64 - sh)))) return true;
                }
            }
        }
        return false;
    }

    // Crane-aware candidate test for the DENSE FALLBACK only: layer l of the
    // candidate vs every occupied cell on layer j >= l, over the whole stay.
    // This deliberately rejects MORE than rule 1 -- including some legal
    // nested/interlocked placements -- and that is its role: it is a
    // placement POLICY, not just a prefilter. Interlocked residents are legal
    // but sticky (rules 4/5 make them constrain every later entry/exit), and
    // a fallback placement is by definition a crowded-moment placement.
    // Removing this in favour of the same-layer test caused a consistent
    // multi-instance regression at both budgets (v1c/v1d: prob_11/34/23/...).
    bool hits(int i, int oi, int jT, int x, int y, int e, int f) {
        Grid& g = bay[jT];
        for (int l = 0; l < nLay[i]; l++) {
            const Mask& mk = masks[i][(size_t)oi * Lmax + l];
            if (!mk.any) continue;
            int bx = x + mk.ox, by = y + mk.oy;
            int sh = bx & 63, w0 = bx >> 6;
            for (int t = e; t < f && t < g.T; t++) {
                for (int my = 0; my < mk.h; my++) {
                    int gy = by + my;
                    if (gy < 0 || gy >= g.H) continue;
                    const uint64_t* src = &mk.rows[(size_t)my * mk.wpr];
                    for (int j = l; j < g.L; j++) {
                        const uint64_t* dst = g.row(j, t, gy);
                        for (int wd = 0; wd < mk.wpr; wd++) {
                            uint64_t v = src[wd];
                            if (!v) continue;
                            if (dst[w0 + wd] & (v << sh)) return true;
                            if (sh && w0 + wd + 1 < g.wpr &&
                                (dst[w0+wd+1] & (v >> (64 - sh)))) return true;
                        }
                    }
                }
            }
        }
        return false;
    }

    void clearBits() {
        for (auto& g : bay) std::fill(g.bits.begin(), g.bits.end(), 0ULL);
        for (auto& v : areaUsed) std::fill(v.begin(), v.end(), 0.0);
    }

    // OR/clear mask of block i (orient oi) at (x,y) for time span [e,f)
    template<bool SET>
    void stampImpl(const Problem& pb, int i, int oi, int jT, int x, int y,
                   int e, int f) {
        Grid& g = bay[jT];
        for (int l = 0; l < nLay[i]; l++) {
            const Mask& mk = masks[i][(size_t)oi * Lmax + l];
            if (!mk.any) continue;
            int bx = x + mk.ox, by = y + mk.oy;   // grid coords of mask origin
            int sh = bx & 63, w0 = bx >> 6;
            for (int t = e; t < f && t < g.T; t++) {
                for (int my = 0; my < mk.h; my++) {
                    int gy = by + my;
                    if (gy < 0 || gy >= g.H) continue;
                    uint64_t* dst = g.row(l, t, gy);
                    const uint64_t* src = &mk.rows[(size_t)my * mk.wpr];
                    for (int wd = 0; wd < mk.wpr; wd++) {
                        uint64_t v = src[wd];
                        if (!v) continue;
                        if (SET) {
                            dst[w0 + wd] |= (v << sh);
                            if (sh && w0 + wd + 1 < g.wpr) dst[w0+wd+1] |= (v >> (64 - sh));
                        } else {
                            dst[w0 + wd] &= ~(v << sh);
                            if (sh && w0 + wd + 1 < g.wpr) dst[w0+wd+1] &= ~(v >> (64 - sh));
                        }
                    }
                }
            }
        }
    }
    void stamp(const Problem& pb, const Sol& s, int i, bool on) {
        if (on) stampImpl<true >(pb, i, s.ori[i], s.bay[i], s.x[i], s.y[i], s.e[i], s.f[i]);
        else    stampImpl<false>(pb, i, s.ori[i], s.bay[i], s.x[i], s.y[i], s.e[i], s.f[i]);
        const auto& g = pb.geo[i].orients[s.ori[i]];
        // area screener currency: TRUE polygon footprint when enabled.
        // With bbox areas the screener is a false bound -- bbox sums
        // reach 103% of bay area on our own boards, so it was
        // rejecting bays that still had genuine room.
        double base = knobs::polyArea() && g.parea > 0.0
                      ? g.parea
                      : (g.bb.x1 - g.bb.x0) * (g.bb.y1 - g.bb.y0);
        double a = base * (on ? 1.0 : -1.0);
        auto& au = areaUsed[s.bay[i]];
        for (int t = s.e[i]; t < s.f[i] && t < T; t++) au[t] += a;
    }
};

} // namespace occ
