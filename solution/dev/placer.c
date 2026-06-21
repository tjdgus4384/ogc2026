/* placer.c -- fast incremental over-time bottom-left placement (the beam-search hot loop).
 * Replicates packer._best_pos (PLACEMENT="bl"): given the currently-placed rectangles (each present
 * over [.,exit)) and a new block's orientation AABBs, find the EARLIEST entry time e (>= release, drawn
 * from {release} U {exits > release}) and the bottom-left (lowest-y, then lowest-x) feasible position.
 * Compiled with: gcc -O3 -shared -fPIC -o placer.so placer.c   (gcc is available on the server).
 * Called via ctypes. ~50-100x faster than the Python version -> makes full beam search viable. */
#include <math.h>
#include <stdlib.h>

/* active: N rows of [X0,Y0,X1,Y1,entry,exit] (placed footprint AABBs in world coords + present window).
 * orients: M rows of [lx0,ly0,lx1,ly1] (candidate orientation AABBs in local coords).
 * A resident constrains the new block (window [e,e+proc)) iff their windows OVERLAP: exit>e && entry<e+proc.
 * Checking only exit>e (the old behaviour) over-constrains -> blocks pushed later -> avoidable tardiness.
 * out[4] <- {x, y, oi, e}.  returns 1 if a candidate-set placement was found, 0 if it fell back. */
int place_one(const double* active, int N, const double* orients, int M,
              double release, double proc, int W, int H, double* out) {
    /* entry candidates = release + (exits > release), sorted ascending, deduped */
    double* cands = (double*)malloc((size_t)(N + 1) * sizeof(double));
    int nc = 0;
    cands[nc++] = release;
    for (int i = 0; i < N; i++) {
        double ex = active[i * 6 + 5];
        if (ex > release) cands[nc++] = ex;
    }
    for (int i = 1; i < nc; i++) {            /* insertion sort */
        double k = cands[i]; int j = i - 1;
        while (j >= 0 && cands[j] > k) { cands[j + 1] = cands[j]; j--; }
        cands[j + 1] = k;
    }
    double* xs = (double*)malloc((size_t)(N + 1) * sizeof(double));
    double* ys = (double*)malloc((size_t)(N + 1) * sizeof(double));
    int found = 0;
    double bx = 0, by = 0; int boi = 0; double be = 0;

    for (int ci = 0; ci < nc && !found; ci++) {
        double e = cands[ci];
        if (ci > 0 && e == cands[ci - 1]) continue;
        for (int m = 0; m < M && !found; m++) {
            double lx0 = orients[m * 4], ly0 = orients[m * 4 + 1];
            double lx1 = orients[m * 4 + 2], ly1 = orients[m * 4 + 3];
            if (ceil(-lx0) > floor(W - lx1) || ceil(-ly0) > floor(H - ly1)) continue;
            /* candidate positions: base corner + (right/top edges of PRESENT blocks), clamped to bay */
            int nx = 0, ny = 0;
            double basex = ceil(-lx0); if (basex < 0) basex = 0;
            double basey = ceil(-ly0); if (basey < 0) basey = 0;
            xs[nx++] = basex; ys[ny++] = basey;
            for (int i = 0; i < N; i++) {
                if (!(active[i * 6 + 5] > e && active[i * 6 + 4] < e + proc)) continue;  /* window disjoint */
                double px = ceil(active[i * 6 + 2] - lx0);     /* place left at its right edge */
                double py = ceil(active[i * 6 + 3] - ly0);     /* place bottom at its top edge */
                if (px + lx0 >= -1e-9 && px + lx1 <= W + 1e-9) xs[nx++] = px;
                if (py + ly0 >= -1e-9 && py + ly1 <= H + 1e-9) ys[ny++] = py;
            }
            /* sort xs, ys ascending (small arrays) */
            for (int a = 1; a < nx; a++) { double k = xs[a]; int j = a - 1; while (j >= 0 && xs[j] > k) { xs[j+1]=xs[j]; j--; } xs[j+1]=k; }
            for (int a = 1; a < ny; a++) { double k = ys[a]; int j = a - 1; while (j >= 0 && ys[j] > k) { ys[j+1]=ys[j]; j--; } ys[j+1]=k; }
            /* bottom-left: lowest y, then lowest x, first feasible (no overlap with present) */
            for (int yi = 0; yi < ny && !found; yi++) {
                double py = ys[yi]; if (yi > 0 && py == ys[yi-1]) continue;
                double Y0 = py + ly0, Y1 = py + ly1;
                for (int xi = 0; xi < nx; xi++) {
                    double px = xs[xi]; if (xi > 0 && px == xs[xi-1]) continue;
                    double X0 = px + lx0, X1 = px + lx1;
                    int ok = 1;
                    for (int i = 0; i < N; i++) {
                        if (!(active[i * 6 + 5] > e && active[i * 6 + 4] < e + proc)) continue;
                        double a0 = active[i*6], b0 = active[i*6+1], a1 = active[i*6+2], b1 = active[i*6+3];
                        if (X0 < a1 - 1e-9 && a0 < X1 - 1e-9 && Y0 < b1 - 1e-9 && b0 < Y1 - 1e-9) { ok = 0; break; }
                    }
                    if (ok) { found = 1; bx = px; by = py; boi = m; be = e; break; }
                }
            }
        }
    }
    if (!found) {            /* fallback: enter when the bay EMPTIES (be >= every exit) -> bay is empty ->
                              * a FITTING orient at its base corner is guaranteed in-bay & collision-free. */
        double maxexit = 0;
        for (int i = 0; i < N; i++) if (active[i*6+5] > maxexit) maxexit = active[i*6+5];
        be = release > maxexit ? release : maxexit;
        int fm = -1;
        for (int m = 0; m < M; m++) {
            double lx0 = orients[m*4], ly0 = orients[m*4+1], lx1 = orients[m*4+2], ly1 = orients[m*4+3];
            if (ceil(-lx0) <= floor(W - lx1) && ceil(-ly0) <= floor(H - ly1)) { fm = m; break; }
        }
        if (fm < 0) fm = 0;                     /* no orient fits the bay at all (shouldn't happen) */
        double lx0 = orients[fm*4], ly0 = orients[fm*4+1];
        bx = ceil(-lx0); if (bx < 0) bx = 0;
        by = ceil(-ly0); if (by < 0) by = 0;
        boi = fm;
    }
    out[0] = bx; out[1] = by; out[2] = (double)boi; out[3] = be;
    free(cands); free(xs); free(ys);
    return found;
}

/* place_many -- expand ONE beam state: place each of K candidate blocks against the SAME `active`, in a
 * single ctypes call (eliminates per-candidate Python<->C round-trips in the beam inner loop).
 *   active   : N rows [X0,Y0,X1,Y1,entry,exit] (the state's placed footprints) -- shared by all candidates
 *   orients_flat : all candidates' orientation AABBs concatenated (4 doubles per orient)
 *   ocount[c]    : number of orientations for candidate c
 *   ooff[c]      : starting orient index (into orients_flat/4) for candidate c
 *   rel[c],proc[c]: release & processing time of candidate c
 *   out      : K rows [x,y,oi,e]  (oi is a LIST POSITION into candidate c's orient set) */
void place_many(const double* active, int N,
                const double* orients_flat, const int* ocount, const int* ooff,
                const double* rel, const double* proc, int K,
                int W, int H, double* out) {
    for (int c = 0; c < K; c++) {
        const double* orients = orients_flat + (size_t)ooff[c] * 4;
        place_one(active, N, orients, ocount[c], rel[c], proc[c], W, H, out + (size_t)c * 4);
    }
}
