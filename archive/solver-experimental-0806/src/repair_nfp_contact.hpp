// repair_nfp_contact.hpp -- contact-driven repair for irregular layered polygons.
//
// Candidate translations are generated from actual layer vertices and edges, not
// resident bounding-box corners.  Integer positions nearest every vertex/edge
// contact locus are retained, then the normal checker-equivalent predicate is
// the authority for feasibility.
#pragma once
#include "repair.hpp"
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <vector>

namespace nfp_contact {

struct Config {
    size_t maxEntryTimes = 48;
    size_t maxCandidates = 768;
    size_t maxContactsPerResident = 192;
    bool includeGeneral = true;
};

struct Stats {
    size_t wallCandidates = 0;
    size_t contactCandidates = 0;
    size_t generalCandidates = 0;
    size_t deduplicated = 0;
    size_t exactChecks = 0;
    size_t exactRejections = 0;
    size_t deadlineStops = 0;
};

struct Candidate {
    int x = 0, y = 0;
    unsigned source = 0; // 1 wall, 2 polygon contact, 4 general
    int contacts = 0;
};

inline bool expired(const repair::Ctx& c, Stats* st) {
    if (!repair::deadlineExpired(c)) return false;
    if (st) ++st->deadlineStops;
    return true;
}

inline void addCandidate(std::vector<Candidate>& out, int x, int y,
                         int xmin, int xmax, int ymin, int ymax,
                         unsigned source, size_t cap, Stats* st) {
    if (x < xmin || x > xmax || y < ymin || y > ymax) return;
    for (Candidate& q : out) {
        if (q.x != x || q.y != y) continue;
        q.source |= source;
        if (st) ++st->deduplicated;
        return;
    }
    if (out.size() >= cap) return;
    out.push_back({x, y, source, 0});
    if (!st) return;
    if (source & 1) ++st->wallCandidates;
    if (source & 2) ++st->contactCandidates;
    if (source & 4) ++st->generalCandidates;
}

inline void nearestIntegers(double value, std::vector<int>& out) {
    const int lo = (int)std::floor(value);
    const int hi = (int)std::ceil(value);
    out.push_back(lo);
    if (hi != lo) out.push_back(hi);
}

// Add integer lattice points closest to p + translation lying on edge [a,b].
// Both forms are used: an A vertex against a resident edge and a resident
// vertex against an A edge.  The resulting finite set represents contact
// loci, including sloped edges, rather than a Cartesian product of bbox axes.
inline void addVertexEdgeLocus(const geom::Pt& p, const geom::Pt& a,
                               const geom::Pt& b, int xmin, int xmax,
                               int ymin, int ymax, unsigned source,
                               size_t cap, std::vector<Candidate>& out,
                               Stats* st) {
    const double dx = b.x - a.x, dy = b.y - a.y;
    const double len2 = dx * dx + dy * dy;
    if (len2 <= geom::EPS) return;
    std::vector<int> lattice;
    // Intersect the contact line with integer x values around the edge's
    // translated endpoints.  The nearest y lattice points cover the discrete
    // placement representation without pretending the line is axis aligned.
    if (std::abs(dx) > geom::EPS) {
        lattice.clear();
        nearestIntegers(a.x - p.x, lattice);
        nearestIntegers(b.x - p.x, lattice);
        for (int x : lattice) {
            const double yline = a.y - p.y +
                dy * (x - (a.x - p.x)) / dx;
            std::vector<int> ys; nearestIntegers(yline, ys);
            for (int y : ys) addCandidate(out, x, y, xmin, xmax, ymin, ymax,
                                          source, cap, st);
        }
    }
    if (std::abs(dy) > geom::EPS) {
        lattice.clear();
        nearestIntegers(a.y - p.y, lattice);
        nearestIntegers(b.y - p.y, lattice);
        for (int y : lattice) {
            const double xline = a.x - p.x +
                dx * (y - (a.y - p.y)) / dy;
            std::vector<int> xs; nearestIntegers(xline, xs);
            for (int x : xs) addCandidate(out, x, y, xmin, xmax, ymin, ymax,
                                          source, cap, st);
        }
    }
}

inline void addWallContacts(const crane::OrientGeom& ga, double W, double H,
                            int xmin, int xmax, int ymin, int ymax,
                            size_t cap, std::vector<Candidate>& out, Stats* st) {
    for (const geom::Poly& layer : ga.layers) for (const geom::Pt& v : layer) {
        std::vector<int> q;
        nearestIntegers(-v.x, q);
        for (int x : q) {
            addCandidate(out, x, ymin, xmin, xmax, ymin, ymax, 1, cap, st);
            addCandidate(out, x, ymax, xmin, xmax, ymin, ymax, 1, cap, st);
        }
        q.clear(); nearestIntegers(W - v.x, q);
        for (int x : q) {
            addCandidate(out, x, ymin, xmin, xmax, ymin, ymax, 1, cap, st);
            addCandidate(out, x, ymax, xmin, xmax, ymin, ymax, 1, cap, st);
        }
        q.clear(); nearestIntegers(-v.y, q);
        for (int y : q) {
            addCandidate(out, xmin, y, xmin, xmax, ymin, ymax, 1, cap, st);
            addCandidate(out, xmax, y, xmin, xmax, ymin, ymax, 1, cap, st);
        }
        q.clear(); nearestIntegers(H - v.y, q);
        for (int y : q) {
            addCandidate(out, xmin, y, xmin, xmax, ymin, ymax, 1, cap, st);
            addCandidate(out, xmax, y, xmin, xmax, ymin, ymax, 1, cap, st);
        }
    }
}

inline void addResidentContacts(const repair::Ctx& c, int A, int oi, int bay,
                                int e, int f, const Config& cfg,
                                int xmin, int xmax, int ymin, int ymax,
                                std::vector<Candidate>& out, Stats* st) {
    const auto& ga = c.pb->geo[A].orients[oi];
    for (int k : c.s->byBay[bay]) {
        if (expired(c, st)) return;
        if (!c.s->placed[k] || !(c.s->e[k] < f && e < c.s->f[k])) continue;
        const auto& gk = c.pb->geo[k].orients[c.s->ori[k]];
        size_t used = 0;
        std::vector<Candidate> symmetric;
        for (const geom::Poly& pa : ga.layers) {
            if (pa.empty()) continue;
            for (size_t ia = 0; ia < pa.size(); ++ia) {
                const geom::Pt& va = pa[ia];
                const geom::Pt& va1 = pa[(ia + 1) % pa.size()];
                for (const geom::Poly& pk : gk.layers) {
                    if (pk.empty()) continue;
                    for (size_t ik = 0; ik < pk.size(); ++ik) {
                        if (used++ >= cfg.maxContactsPerResident) goto resident_done;
                        const geom::Pt b0{pk[ik].x + c.s->x[k], pk[ik].y + c.s->y[k]};
                        const geom::Pt b1{pk[(ik + 1) % pk.size()].x + c.s->x[k],
                                          pk[(ik + 1) % pk.size()].y + c.s->y[k]};
                        // A vertex against resident edge.
                        addVertexEdgeLocus(va, b0, b1, xmin, xmax, ymin, ymax,
                                           2, cfg.maxCandidates, out, st);
                        // Resident vertex against A edge.  Generate in the
                        // negated frame, then negate the translations below.
                        addVertexEdgeLocus(b0, va, va1, -xmax, -xmin,
                                           -ymax, -ymin, 2, cfg.maxCandidates,
                                           symmetric, nullptr);
                    }
                }
            }
        }
resident_done:
        for (const Candidate& q : symmetric)
            addCandidate(out, -q.x, -q.y, xmin, xmax, ymin, ymax, 2,
                         cfg.maxCandidates, st);
    }
}

inline int contactCount(const repair::Ctx& c, int A, int oi, int bay, int e, int f,
                        int x, int y) {
    const auto& ga = c.pb->geo[A].orients[oi];
    int count = 0;
    for (int k : c.s->byBay[bay]) {
        if (repair::deadlineExpired(c)) return count;
        if (!c.s->placed[k] || !(c.s->e[k] < f && e < c.s->f[k])) continue;
        const auto& gk = c.pb->geo[k].orients[c.s->ori[k]];
        for (const auto& pa : ga.layers) for (const geom::Pt& va : pa)
            for (const auto& pk : gk.layers) for (size_t i = 0; i < pk.size(); ++i) {
                const geom::Pt& b0 = pk[i]; const geom::Pt& b1 = pk[(i + 1) % pk.size()];
                const double cross = (b1.x - b0.x) * (va.y + y - b0.y - c.s->y[k]) -
                                     (b1.y - b0.y) * (va.x + x - b0.x - c.s->x[k]);
                if (std::abs(cross) < 1e-7) ++count;
            }
    }
    return count;
}

inline double score(const repair::Ctx& c, int A, int bay, int oi, int e,
                    const Candidate& q) {
    const auto& pb = *c.pb;
    const auto& g = pb.geo[A].orients[oi];
    const double tardiness = std::max(0, e + pb.P[A] - pb.D[A]);
    double z2 = 0.0;
    std::vector<double> load(pb.m, 0.0);
    for (int i = 0; i < pb.n; ++i)
        if (c.s->placed[i]) load[c.s->bay[i]] += pb.workload[i];
    load[bay] += pb.workload[A];
    for (int i = 0; i < pb.m; ++i) for (int j = i + 1; j < pb.m; ++j)
        z2 = std::max(z2, std::fabs(pb.u[i] * load[i] - pb.u[j] * load[j]));
    const double immediate = pb.w1 * tardiness + pb.w2 * std::floor(z2) +
                             pb.w3 * (pb.Smax[A] - pb.pref[A][bay]);
    const double l = g.bb.x0 + q.x, r = pb.bayW[bay] - g.bb.x1 - q.x;
    const double b = g.bb.y0 + q.y, t = pb.bayH[bay] - g.bb.y1 - q.y;
    // Preserve one large connected rectangular residual region rather than
    // merely minimizing wall distance; exact feasibility remains authoritative.
    const double residual = std::max(l * pb.bayH[bay], r * pb.bayH[bay]) +
                            std::max(b * pb.bayW[bay], t * pb.bayW[bay]);
    int future = 0;
    for (int k : c.s->byBay[bay]) if (c.s->placed[k] &&
        ((e < c.s->e[k] && c.s->e[k] < e + pb.P[A]) ||
         (e < c.s->f[k] && c.s->f[k] <= e + pb.P[A]))) {
        bool blocks = false;
        crane::pairMemo(g, q.x, q.y, pb.geo[k].orients[c.s->ori[k]],
                        c.s->x[k], c.s->y[k], 1, blocks);
        if (blocks) ++future;
    }
    return immediate + 0.02 * future - 0.01 * q.contacts -
           0.00001 * residual;
}

inline bool temporarilyFeasible(repair::Ctx& c, int A, int bay, int oi, int x, int y,
                                int e, Stats* st) {
    core::Sol& s = *c.s; const core::Problem& pb = *c.pb;
    s.bay[A] = bay; s.ori[A] = oi; s.x[A] = x; s.y[A] = y;
    s.e[A] = e; s.f[A] = e + pb.P[A]; s.placed[A] = 1; s.addIdx(A);
    if (st) ++st->exactChecks;
    const bool ok = repair::placementFeasible(c, A);
    s.delIdx(A); s.placed[A] = 0;
    if (!ok && st) ++st->exactRejections;
    return ok;
}

inline bool insert(repair::Ctx& c, int A, const Config& cfg = Config(), Stats* st = nullptr) {
    if (A < 0 || A >= c.pb->n || c.s->placed[A] || expired(c, st)) return false;
    core::Sol& s = *c.s; const core::Problem& pb = *c.pb;
    const int oldBay = s.bay[A], oldOri = s.ori[A], oldX = s.x[A], oldY = s.y[A];
    const int oldE = s.e[A], oldF = s.f[A];
    std::vector<int> entries{pb.R[A]};
    for (int j = 0; j < pb.m; ++j) for (int k : s.byBay[j])
        if (s.placed[k] && s.f[k] >= pb.R[A]) entries.push_back(s.f[k]);
    std::sort(entries.begin(), entries.end());
    entries.erase(std::unique(entries.begin(), entries.end()), entries.end());
    if (entries.size() > cfg.maxEntryTimes) entries.resize(cfg.maxEntryTimes);
    Candidate best; int bestBay = -1, bestOri = -1, bestE = -1;
    double bestScore = std::numeric_limits<double>::infinity();
    for (int e : entries) {
        if (e + pb.P[A] > c.horizon) continue;
        if (c.entryCap && c.entryCap[A] >= 0 && e > c.entryCap[A]) break;
        for (int bay = 0; bay < pb.m; ++bay) {
            if (c.forcedBay && c.forcedBay[A] >= 0 && c.forcedBay[A] != bay) continue;
            for (int oi = 0; oi < (int)pb.geo[A].orients.size(); ++oi) {
                if (expired(c, st)) goto restore;
                const auto& g = pb.geo[A].orients[oi];
                const int xmin = (int)std::ceil(-g.bb.x0), xmax = (int)std::floor(pb.bayW[bay] - g.bb.x1);
                const int ymin = (int)std::ceil(-g.bb.y0), ymax = (int)std::floor(pb.bayH[bay] - g.bb.y1);
                if (xmin > xmax || ymin > ymax) continue;
                std::vector<Candidate> cs;
                addWallContacts(g, pb.bayW[bay], pb.bayH[bay], xmin, xmax, ymin, ymax, cfg.maxCandidates, cs, st);
                addResidentContacts(c, A, oi, bay, e, e + pb.P[A], cfg, xmin, xmax, ymin, ymax, cs, st);
                if (cfg.includeGeneral) {
                    addCandidate(cs, (xmin + xmax) / 2, (ymin + ymax) / 2, xmin, xmax, ymin, ymax, 4, cfg.maxCandidates, st);
                    addCandidate(cs, xmin, ymin, xmin, xmax, ymin, ymax, 4, cfg.maxCandidates, st);
                    addCandidate(cs, xmax, ymax, xmin, xmax, ymin, ymax, 4, cfg.maxCandidates, st);
                }
                for (Candidate& q : cs) {
                    if (expired(c, st)) goto restore;
                    q.contacts = contactCount(c, A, oi, bay, e, e + pb.P[A], q.x, q.y);
                    if (!temporarilyFeasible(c, A, bay, oi, q.x, q.y, e, st)) continue;
                    const double v = score(c, A, bay, oi, e, q);
                    if (v < bestScore || (v == bestScore &&
                        (e < bestE || (e == bestE && (bay < bestBay ||
                         (bay == bestBay && (oi < bestOri || (oi == bestOri &&
                          (q.y < best.y || (q.y == best.y && q.x < best.x)))))))))) {
                        bestScore = v; best = q; bestBay = bay; bestOri = oi; bestE = e;
                    }
                }
            }
        }
    }
    if (bestBay >= 0) {
        s.bay[A] = bestBay; s.ori[A] = bestOri; s.x[A] = best.x; s.y[A] = best.y;
        s.e[A] = bestE; s.f[A] = bestE + pb.P[A]; s.placed[A] = 1; s.addIdx(A);
        c.oc->stamp(pb, s, A, true);
        return true;
    }
restore:
    s.bay[A] = oldBay; s.ori[A] = oldOri; s.x[A] = oldX; s.y[A] = oldY;
    s.e[A] = oldE; s.f[A] = oldF; s.placed[A] = 0;
    return false;
}

} // namespace nfp_contact
