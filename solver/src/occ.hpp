// occ.hpp -- bit-packed occupancy grid per (bay, layer, time) with
// under-approximate block masks (cell strictly inside the polygon).
//
// Mask AND-hit => both polygons strictly contain that cell centre => certain
// positive-area SAME-LAYER overlap => the placement is certainly infeasible
// (checker rule 1). So the prefilter's rejections are always sound; anything
// it admits still goes through vpConservative. One prefilter, one semantic.
#pragma once
#include "core.hpp"
#include <vector>
#include <cstdint>

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
        double a = (g.bb.x1 - g.bb.x0) * (g.bb.y1 - g.bb.y0) * (on ? 1.0 : -1.0);
        auto& au = areaUsed[s.bay[i]];
        for (int t = s.e[i]; t < s.f[i] && t < T; t++) au[t] += a;
    }
};

} // namespace occ
