/* geom.c -- fast bit-packed union-footprint bitmap packing kernel (crane-aware via union-disjointness).
 *
 * Occupancy = bit array, Wwords uint64 per row, H rows. A block's footprint = a list of set cells (dx,dy)
 * relative to its ref. Placement test = AND the block's cells against occ bits (O(ncells), each O(1) bit
 * lookup). Bottom-left scan over candidate (x,y). ~64x denser + cache-friendlier than a byte bitmap.
 *
 * Compiled to libgeom.so, called via ctypes. */
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

/* can the block place with ref at (x,y)?  BOUNDS use the REAL footprint extent (rmaxx,rmaxy = real polygon
 * max relative to the cell origin) so a block that geometrically fits the bay is not rejected by the
 * conservative mask's +1-cell inflation. OVERLAP uses the inflated cells, but cells beyond the grid are
 * inflation-only (the real bound guarantees no real content there) so they are skipped, not read OOB. */
static inline int fits(const uint64_t* occ, int W, int H, int Wwords,
                       const int* cdx, const int* cdy, int ncells, int x, int y,
                       double rmaxx, double rmaxy) {
    if (x < 0 || y < 0 || x + rmaxx > W + 1e-6 || y + rmaxy > H + 1e-6) return 0;
    for (int i = 0; i < ncells; i++) {
        int cx = x + cdx[i], cy = y + cdy[i];
        if (cx >= W || cy >= H) continue;                 /* inflation cell beyond grid: no real content */
        if (occ[(size_t)cy * Wwords + (cx >> 6)] & (1ULL << (cx & 63))) return 0;
    }
    return 1;
}

static inline void set_cells(uint64_t* occ, int W, int H, int Wwords,
                             const int* cdx, const int* cdy, int ncells, int x, int y) {
    for (int i = 0; i < ncells; i++) {
        int cx = x + cdx[i], cy = y + cdy[i];
        if (cx < 0 || cx >= W || cy < 0 || cy >= H) continue;          /* bounds-safe (never write OOB) */
        occ[(size_t)cy * Wwords + (cx >> 6)] |= (1ULL << (cx & 63));
    }
}

/* bottom-left first-free ref position for a footprint. out[0]=x, out[1]=y, or out[0]=-1 if none. */
void bl_place(const uint64_t* occ, int W, int H, int Wwords,
              const int* cdx, const int* cdy, int ncells, double rmaxx, double rmaxy, int* out) {
    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            if (fits(occ, W, H, Wwords, cdx, cdy, ncells, x, y, rmaxx, rmaxy)) { out[0] = x; out[1] = y; return; }
        }
    }
    out[0] = -1; out[1] = -1;
}

/* ---- BITMASK fast path: a block footprint as per-row column bitmasks (bw bits/row, bw_words words/row, at
 * the block's local origin dx in [0,bw)). Placement test at (x,y) shifts each block row left by x bits and
 * ANDs against the occ row -- O(bh * bw_words) word-ops per position instead of O(ncells) bit lookups. This
 * is the bottom-left-scan hot path; it is mathematically identical to fits() (same cells), just word-parallel. */
typedef struct { int bh, bw, bw_words; const uint64_t* rows; double rmaxx, rmaxy; } BMask;

static inline int fits_bm(const uint64_t* occ, int W, int H, int Wwords, const BMask* m, int x, int y) {
    if (x < 0 || y < 0 || x + m->rmaxx > W + 1e-6 || y + m->rmaxy > H + 1e-6) return 0;
    int sh = x & 63, wo = x >> 6, bww = m->bw_words;
    for (int dy = 0; dy < m->bh; dy++) {
        const uint64_t* br = m->rows + (size_t)dy * bww;
        const uint64_t* orow = occ + (size_t)(y + dy) * Wwords;
        for (int w = 0; w < bww; w++) {
            uint64_t v = br[w];
            if (!v) continue;
            int ow = wo + w;
            uint64_t lo = v << sh;
            if (ow < Wwords && (orow[ow] & lo)) return 0;
            if (sh) { uint64_t hi = v >> (64 - sh); if (ow + 1 < Wwords && (orow[ow + 1] & hi)) return 0; }
        }
    }
    return 1;
}

static inline void bl_place_bm(const uint64_t* occ, int W, int H, int Wwords, const BMask* m, int* out) {
    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            if (fits_bm(occ, W, H, Wwords, m, x, y)) { out[0] = x; out[1] = y; return; }
        }
    }
    out[0] = -1; out[1] = -1;
}

/* SPATIAL benchmark / single-occupancy pack: place nblocks footprints (each a slice of the flat cell arrays
 * cdx/cdy via offs[]) bottom-left into one shared occupancy; write ref (x,y) per block into pos[2*i].
 * (No time windows -- pure spatial speed test of the kernel.) */
void pack_spatial(int W, int H, int Wwords,
                  const int* cdx, const int* cdy, const int* offs, int nblocks, int* pos) {
    size_t nwords = (size_t)Wwords * H;
    uint64_t* occ = (uint64_t*)__builtin_alloca(nwords * 8);
    memset(occ, 0, nwords * 8);
    for (int b = 0; b < nblocks; b++) {
        int s = offs[b], e = offs[b + 1], nc = e - s;
        int o[2];
        bl_place(occ, W, H, Wwords, cdx + s, cdy + s, nc, (double)W, (double)H, o);
        pos[2 * b] = o[0]; pos[2 * b + 1] = o[1];
        if (o[0] >= 0) set_cells(occ, W, H, Wwords, cdx + s, cdy + s, nc, o[0], o[1]);
    }
}


/* TIME-WINDOWED bay pack. Blocks given in placement (EDD) order; each has a slice of cells (offs[]), a proc
 * time P[], a release r[]. For each block, try entry candidates {release} U {exit of already-placed > release},
 * earliest first; at candidate e (window [e,e+P)), occupancy = union of co-present placed blocks' cells; place
 * bottom-left. Crane-clean by union-footprint disjointness among co-present blocks. Writes pos[3*b]=x,y,e. */
void pack_bay_time(int W, int H, int Wwords,
                   const int* cdx, const int* cdy, const int* offs,
                   const long* P, const long* r, int nblocks, long* pos) {
    size_t nwords = (size_t)Wwords * H;
    uint64_t* occ = (uint64_t*)__builtin_alloca(nwords * 8);
    /* placed block bookkeeping */
    long* pe = (long*)__builtin_alloca(sizeof(long) * nblocks);   /* entry */
    long* pf = (long*)__builtin_alloca(sizeof(long) * nblocks);   /* exit  */
    int*  px = (int*)__builtin_alloca(sizeof(int) * nblocks);
    int*  py = (int*)__builtin_alloca(sizeof(int) * nblocks);
    long cand[4096];
    for (int b = 0; b < nblocks; b++) {
        int s = offs[b], e2 = offs[b + 1], nc = e2 - s;
        /* build sorted-unique entry candidates */
        int ncand = 0; cand[ncand++] = r[b];
        for (int j = 0; j < b; j++) if (pf[j] > r[b]) cand[ncand++] = pf[j];
        /* insertion sort + dedup */
        for (int i = 1; i < ncand; i++) { long k = cand[i]; int q = i - 1;
            while (q >= 0 && cand[q] > k) { cand[q + 1] = cand[q]; q--; } cand[q + 1] = k; }
        long best_e = -1; int bx = -1, by = -1;
        long prev = -1;
        for (int ci = 0; ci < ncand; ci++) {
            long e = cand[ci]; if (e == prev) continue; prev = e;
            long f = e + P[b];
            memset(occ, 0, nwords * 8);
            for (int j = 0; j < b; j++) {
                if (pf[j] > e && pe[j] < f && px[j] >= 0) {
                    int js = offs[j], je = offs[j + 1];
                    set_cells(occ, W, H, Wwords, cdx + js, cdy + js, je - js, px[j], py[j]);
                }
            }
            int o[2]; bl_place(occ, W, H, Wwords, cdx + s, cdy + s, nc, (double)W, (double)H, o);
            if (o[0] >= 0) { best_e = e; bx = o[0]; by = o[1]; break; }
        }
        if (best_e < 0) { best_e = cand[0]; bx = 0; by = 0; px[b] = -1; }  /* unplaced: not a resident */
        else px[b] = bx;
        pe[b] = best_e; pf[b] = best_e + P[b]; py[b] = by;
        pos[3 * b] = bx; pos[3 * b + 1] = by; pos[3 * b + 2] = best_e;
    }
}


/* MULTI-ORIENT time-windowed pack. Each block b has orientations k in [ostart[b], ostart[b+1]); orient-
 * instance k's cells are cdx[oo[k]..oo[k+1]]. Blocks are PLACED in the sequence given by order[] (a
 * permutation of 0..nblocks-1): all geometry arrays stay indexed by stable block id b, only the placement
 * sequence changes -- so a search can precompute the geometry ONCE and re-pack a new order by passing only
 * the n-int order[] (no per-iter geometry re-marshaling). For each block, earliest entry candidate, try
 * each orient, take the first that places bottom-left feasibly. pos[4*b] = x,y,entry, orient_local. */
void pack_bay_time_mo_ord(int W, int H, int Wwords,
                          const int* cdx, const int* cdy, const int* oo, const int* ostart,
                          const double* rmx, const double* rmy,
                          const long* P, const long* r, const int* order, int nblocks, long* pos) {
    size_t nwords = (size_t)Wwords * H;
    uint64_t* occ = (uint64_t*)__builtin_alloca(nwords * 8);
    long* pe = (long*)__builtin_alloca(sizeof(long) * nblocks);
    long* pf = (long*)__builtin_alloca(sizeof(long) * nblocks);
    int*  px = (int*)__builtin_alloca(sizeof(int) * nblocks);
    int*  py = (int*)__builtin_alloca(sizeof(int) * nblocks);
    int*  pk = (int*)__builtin_alloca(sizeof(int) * nblocks);   /* chosen orient-instance index */
    long cand[8192];
    /* Build per-orient row bitmasks ONCE (the bottom-left scan then uses the word-parallel fits_bm). malloc
     * (not alloca) for the arena so large instances cannot overflow the stack; rebuilt per pack but the cost
     * is ~total-cell-count, negligible vs the scan it accelerates. */
    int norients = ostart[nblocks];
    BMask* masks = (BMask*)malloc(sizeof(BMask) * (norients > 0 ? norients : 1));
    size_t totw = 0;
    for (int k = 0; k < norients; k++) {
        int s = oo[k], e2 = oo[k + 1], bw = 0, bh = 0;
        for (int i = s; i < e2; i++) { if (cdx[i] + 1 > bw) bw = cdx[i] + 1; if (cdy[i] + 1 > bh) bh = cdy[i] + 1; }
        if (bw < 1) bw = 1; if (bh < 1) bh = 1;
        int bww = (bw + 63) / 64;
        masks[k].bw = bw; masks[k].bh = bh; masks[k].bw_words = bww;
        masks[k].rmaxx = rmx[k]; masks[k].rmaxy = rmy[k];
        totw += (size_t)bh * bww;
    }
    uint64_t* arena = (uint64_t*)malloc((totw ? totw : 1) * 8);
    memset(arena, 0, (totw ? totw : 1) * 8);
    { size_t off = 0;
      for (int k = 0; k < norients; k++) {
          uint64_t* rows = arena + off; masks[k].rows = rows;
          int bww = masks[k].bw_words, s = oo[k], e2 = oo[k + 1];
          for (int i = s; i < e2; i++) { int dx = cdx[i], dy = cdy[i]; rows[(size_t)dy * bww + (dx >> 6)] |= (1ULL << (dx & 63)); }
          off += (size_t)masks[k].bh * bww;
      } }
    for (int ki = 0; ki < nblocks; ki++) {
        int b = order[ki];
        int ncand = 0; cand[ncand++] = r[b];
        for (int kj = 0; kj < ki; kj++) { int j = order[kj]; if (pf[j] > r[b]) cand[ncand++] = pf[j]; }
        for (int i = 1; i < ncand; i++) { long k = cand[i]; int q = i - 1;
            while (q >= 0 && cand[q] > k) { cand[q + 1] = cand[q]; q--; } cand[q + 1] = k; }
        long best_e = -1; int bx = -1, by = -1, bk = -1; long prev = -1;
        for (int ci = 0; ci < ncand && best_e < 0; ci++) {
            long e = cand[ci]; if (e == prev) continue; prev = e;
            long f = e + P[b];
            memset(occ, 0, nwords * 8);
            for (int kj = 0; kj < ki; kj++) {
                int j = order[kj];
                if (pf[j] > e && pe[j] < f && px[j] >= 0) {
                    int ks = pk[j]; set_cells(occ, W, H, Wwords, cdx + oo[ks], cdy + oo[ks], oo[ks + 1] - oo[ks], px[j], py[j]);
                }
            }
            for (int k = ostart[b]; k < ostart[b + 1]; k++) {
                int o[2]; bl_place_bm(occ, W, H, Wwords, &masks[k], o);
                if (o[0] >= 0) { best_e = e; bx = o[0]; by = o[1]; bk = k; break; }
            }
        }
        if (best_e < 0) {
            /* DELAY-TO-EMPTY: enter after the latest current exit so the bay is (near) empty -> place there.
             * Guarantees a feasible placement (if the block fits the empty bay) at the cost of tardiness;
             * the search avoids such assignments via their high Z1. */
            long le = 0; for (int kj = 0; kj < ki; kj++) { int j = order[kj]; if (pf[j] > le) le = pf[j]; }
            if (le < r[b]) le = r[b];
            memset(occ, 0, nwords * 8);
            for (int kj = 0; kj < ki; kj++) { int j = order[kj];
                if (pf[j] > le && pe[j] < le + P[b] && px[j] >= 0) {
                    int ks = pk[j]; set_cells(occ, W, H, Wwords, cdx + oo[ks], cdy + oo[ks], oo[ks+1]-oo[ks], px[j], py[j]); } }
            for (int k = ostart[b]; k < ostart[b + 1]; k++) {
                int o[2]; bl_place_bm(occ, W, H, Wwords, &masks[k], o);
                if (o[0] >= 0) { best_e = le; bx = o[0]; by = o[1]; bk = k; break; }
            }
        }
        if (best_e < 0) { px[b] = -1; pe[b] = 0; pf[b] = 0; py[b] = 0; pk[b] = ostart[b];
                          pos[4 * b] = -1; continue; }   /* defer to serial-stack pass below */
        px[b] = bx; pe[b] = best_e; pf[b] = best_e + P[b]; py[b] = by; pk[b] = bk;
        pos[4 * b] = bx; pos[4 * b + 1] = by; pos[4 * b + 2] = best_e; pos[4 * b + 3] = bk - ostart[b];
    }
    /* SERIAL-STACK the deferred (unplaceable-in-context) blocks: place each ALONE, one after another, after
     * everything else. Single occupancy => always feasible (if it fits the empty bay) with large tardiness;
     * the directed search avoids orders that defer blocks via the resulting high Z1. */
    long run_t = 0; for (int b = 0; b < nblocks; b++) if (pf[b] > run_t) run_t = pf[b];
    for (int ki = 0; ki < nblocks; ki++) {
        int b = order[ki];
        if (pos[4 * b] != -1) continue;
        long e = run_t > r[b] ? run_t : r[b];
        memset(occ, 0, nwords * 8);
        int bx = -1, by = -1, bk = ostart[b];
        for (int k = ostart[b]; k < ostart[b + 1]; k++) {
            int o[2]; bl_place_bm(occ, W, H, Wwords, &masks[k], o);
            if (o[0] >= 0) { bx = o[0]; by = o[1]; bk = k; break; }
        }
        if (bx < 0) { bx = 0; by = 0; bk = ostart[b]; }   /* truly too big (shouldn't happen): least-bad */
        pos[4 * b] = bx; pos[4 * b + 1] = by; pos[4 * b + 2] = e; pos[4 * b + 3] = bk - ostart[b];
        run_t = e + P[b];
    }
    free(arena); free(masks);
}

/* Back-compat shim: identity order (blocks placed in index order). */
void pack_bay_time_mo(int W, int H, int Wwords,
                      const int* cdx, const int* cdy, const int* oo, const int* ostart,
                      const double* rmx, const double* rmy,
                      const long* P, const long* r, int nblocks, long* pos) {
    int* order = (int*)__builtin_alloca(sizeof(int) * nblocks);
    for (int i = 0; i < nblocks; i++) order[i] = i;
    pack_bay_time_mo_ord(W, H, Wwords, cdx, cdy, oo, ostart, rmx, rmy, P, r, order, nblocks, pos);
}
