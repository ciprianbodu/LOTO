// Recoacere simulata pe sansa EXACTA ca cel putin un bilet sa atinga >= t,
// la buget fix de bilete, pe aceeasi functie ca covering/anneal_hits.py si
// covering/budget_climb.py: subseturile pool-ului (marimi 1..draw_n) cu
// ponderea C(max_num - v, draw_n - |s|); P(>=t) = suma ponderilor subseturilor
// pe care un bilet le atinge in >= t locuri / C(max_num, draw_n).
// Contorii sunt intregi (uint64), deci P e exacta, fara deriva.
//
//   gcc -O2 -o budget_anneal budget_anneal.c -lm
//   ./budget_anneal v pick draw_n max_num g moves seed targeted_frac t0_factor
//                   t_ratio penalty cycles start.txt out.txt [primary]
// `primary` (optional, implicit g): pragul urmarit intai. Pentru un cover
// complet (P(>= g) e deja maximul pool-ului) se da g+1: mutarile care strica
// acoperirea sunt penalizate, iar cel mai bun design fezabil o pastreaza.
// start.txt / out.txt: un bilet pe linie, pozitii 1..v (1 = cea mai buna).
// Cel mai bun design FEZABIL (W[t] >= baza la fiecare t de la 3 la top si
// acoperirea garantiei >= baza) e scris; altfel se scrie biletul de start.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <stdint.h>

static int v, pick, draw_n, max_num, g, top;
static int n;                   // numar de subseturi
static int *sidx;               // mask -> index (-1 daca nu)
static uint32_t *smask;         // index -> mask
static uint8_t *ssize;
static uint64_t *sweight;
static double total;
static int T;
static uint32_t *tick;
static int8_t *inter;           // T x n
static int16_t *cnt;            // (top+1) x n
static uint64_t W[8], baseW[8];
static int cov, basecov, nG;
static int *unc_list, *unc_pos, nunc;   // g-subseturi neacoperite (indici)
static int *gidx; static int ng;
static int *L[16][16]; static int Ln[16][16];
static int occupied[16];
static uint8_t present[1 << 16];
static uint64_t rng;
static double wt[8], penalty;

static inline uint64_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }
static inline double urand(void) { return (rnd() >> 11) * (1.0 / 9007199254740992.0); }
static uint64_t binom(int a, int b) { if (b < 0 || b > a) return 0; uint64_t r = 1; for (int i = 1; i <= b; i++) r = r * (a - b + i) / i; return r; }
static void unc_add(int i) { unc_pos[i] = nunc; unc_list[nunc++] = i; }
static void unc_del(int i) { int p = unc_pos[i]; int last = unc_list[--nunc]; unc_list[p] = last; unc_pos[last] = p; unc_pos[i] = -1; }

static double objective(const uint64_t *w, int c) {
    double value = 0, loss = 0;
    for (int t = 3; t <= top; t++) {
        value += wt[t] * (double)w[t] / total;
        if (w[t] < baseW[t]) loss += (double)(baseW[t] - w[t]) / total;
    }
    if (c < basecov) loss += (double)(basecov - c) / nG;
    return value - penalty * loss;
}
static int feasible(const uint64_t *w, int c) {
    for (int t = 3; t <= top; t++) if (w[t] < baseW[t]) return 0;
    return c >= basecov;
}

int main(int argc, char **argv) {
    if (argc < 15) { fprintf(stderr, "args\n"); return 2; }
    v = atoi(argv[1]); pick = atoi(argv[2]); draw_n = atoi(argv[3]); max_num = atoi(argv[4]); g = atoi(argv[5]);
    long long moves = atoll(argv[6]); uint64_t seed = strtoull(argv[7], 0, 10);
    double targeted = atof(argv[8]), t0f = atof(argv[9]), tratio = atof(argv[10]); penalty = atof(argv[11]);
    int cycles = atoi(argv[12]); const char *startf = argv[13], *outf = argv[14];
    top = pick < draw_n ? pick : draw_n;
    rng = seed * 0x9E3779B97F4A7C15ull + 0x1234567ull; if (!rng) rng = 1;
    for (int i = 0; i < 20; i++) rnd();
    int outside = max_num - v;
    total = (double)binom(max_num, draw_n);
    sidx = malloc(sizeof(int) << v); n = 0;
    for (uint32_t m = 1; m < (1u << v); m++) { int s = __builtin_popcount(m); sidx[m] = (s <= draw_n && draw_n - s <= outside) ? n++ : -1; }
    smask = malloc(n * sizeof(uint32_t)); ssize = malloc(n); sweight = malloc(n * sizeof(uint64_t));
    for (uint32_t m = 1; m < (1u << v); m++) if (sidx[m] >= 0) { int i = sidx[m]; smask[i] = m; ssize[i] = __builtin_popcount(m); sweight[i] = binom(outside, draw_n - ssize[i]); }
    nG = (int)binom(v, g);
    gidx = malloc(nG * sizeof(int)); ng = 0;
    for (int i = 0; i < n; i++) if (ssize[i] == g) gidx[ng++] = i;
    for (int x = 0; x < v; x++) for (int y = 0; y < v; y++) if (x != y) {
        int c = 0; for (int i = 0; i < n; i++) if ((smask[i] >> x & 1) && !(smask[i] >> y & 1)) c++;
        L[x][y] = malloc(c * sizeof(int)); Ln[x][y] = 0;
        for (int i = 0; i < n; i++) if ((smask[i] >> x & 1) && !(smask[i] >> y & 1)) L[x][y][Ln[x][y]++] = i;
    }
    // bilete de start
    FILE *f = fopen(startf, "r"); if (!f) { perror("start"); return 2; }
    tick = malloc(1024 * sizeof(uint32_t)); T = 0; char line[1024];
    while (fgets(line, sizeof line, f)) { uint32_t m = 0; int c = 0; char *p = line; while (*p) { while (*p == ' ' || *p == '\t') p++; if (*p < '0' || *p > '9') break; int q = strtol(p, &p, 10); m |= 1u << (q - 1); c++; } if (c == pick) tick[T++] = m; }
    fclose(f);
    inter = malloc((size_t)T * n); cnt = calloc((size_t)(top + 1) * n, sizeof(int16_t));
    memset(present, 0, sizeof present);
    for (int i = 0; i < T; i++) { present[tick[i]] = 1; for (int p = 0; p < v; p++) occupied[p] += tick[i] >> p & 1;
        for (int s = 0; s < n; s++) { int h = __builtin_popcount(smask[s] & tick[i]); inter[(size_t)i * n + s] = h; for (int t = 1; t <= h; t++) cnt[t * n + s]++; } }
    memset(W, 0, sizeof W); cov = 0;
    for (int s = 0; s < n; s++) for (int t = 1; t <= top; t++) if (cnt[t * n + s] > 0) W[t] += sweight[s];
    unc_list = malloc(n * sizeof(int)); unc_pos = malloc(n * sizeof(int)); nunc = 0;
    for (int s = 0; s < n; s++) unc_pos[s] = -1;
    for (int j = 0; j < ng; j++) { int s = gidx[j]; if (cnt[g * n + s] > 0) cov++; else unc_add(s); }
    memcpy(baseW, W, sizeof W); basecov = cov;
    int primary = argc > 15 ? atoi(argv[15]) : g;
    if (primary < 3) primary = 3;
    if (primary > top) primary = top;
    for (int t = 3; t <= top; t++) wt[t] = (t == primary) ? 1.0 : (t > primary ? pow(1e-3, t - primary) : pow(1e-4, primary - t));
    printf("start T=%d n=%d base", T, n); for (int t = 3; t <= top; t++) printf(" P%d=%.9f", t, (double)W[t] / total); printf(" cov=%d/%d\n", cov, nG);
    double curf = objective(W, cov), bestf = curf; uint32_t *best = malloc(T * sizeof(uint32_t)); memcpy(best, tick, T * sizeof(uint32_t));
    uint64_t bestW[8]; memcpy(bestW, W, sizeof W); int bestcov = cov;
    double t0 = t0f * (double)baseW[primary] / total; if (t0 < 1e-9) t0 = 1e-9; double t1 = t0 / tratio;
    long long accepted = 0, tried = 0, percycle = moves / (cycles > 0 ? cycles : 1);
    clock_t c0 = clock();
    for (long long k = 0; k < moves; k++) {
        long long kk = k % percycle; double temp = t0 * pow(t1 / t0, (double)kk / (double)(percycle > 1 ? percycle - 1 : 1));
        int i, x, y; uint32_t mask;
        if (urand() < targeted && nunc > 0) {
            int s = unc_list[(int)(urand() * nunc)]; uint32_t tm = smask[s];
            int near[1024], nn = 0; for (int j = 0; j < T; j++) if (inter[(size_t)j * n + s] == g - 1) near[nn++] = j;
            if (!nn) continue;
            i = near[(int)(urand() * nn)]; mask = tick[i];
            uint32_t miss = tm & ~mask; y = __builtin_ctz(miss);
            int outs[16], no = 0; for (int p = 0; p < v; p++) if ((mask >> p & 1) && !(tm >> p & 1)) outs[no++] = p;
            x = outs[(int)(urand() * no)];
        } else {
            i = (int)(urand() * T); mask = tick[i];
            int ins[16], ni = 0, outs[16], no = 0; for (int p = 0; p < v; p++) { if (mask >> p & 1) ins[ni++] = p; else outs[no++] = p; }
            x = ins[(int)(urand() * ni)]; y = outs[(int)(urand() * no)];
        }
        if (occupied[x] == 1) continue;
        uint32_t moved = (mask & ~(1u << x)) | (1u << y);
        if (present[moved]) continue;
        tried++;
        uint64_t nW[8]; memcpy(nW, W, sizeof W); int ncov = cov;
        const int8_t *row = inter + (size_t)i * n;
        const int *lose = L[x][y]; int nl = Ln[x][y];
        for (int j = 0; j < nl; j++) { int s = lose[j]; int old = row[s]; if (old >= 3 || old == g) { if (cnt[old * n + s] == 1) { nW[old] -= sweight[s]; if (old == g && ssize[s] == g) ncov--; } } }
        const int *gain = L[y][x]; int ngn = Ln[y][x];
        for (int j = 0; j < ngn; j++) { int s = gain[j]; int nt = row[s] + 1; if (nt >= 3 || nt == g) { if (cnt[nt * n + s] == 0) { nW[nt] += sweight[s]; if (nt == g && ssize[s] == g) ncov++; } } }
        double nf = objective(nW, ncov), d = nf - curf;
        if (d >= 0 || urand() < exp(d / temp)) {
            int8_t *wrow = inter + (size_t)i * n;
            for (int j = 0; j < nl; j++) { int s = lose[j]; int old = wrow[s]; wrow[s] = old - 1; int16_t *c = &cnt[old * n + s]; if (--*c == 0 && old == g && ssize[s] == g) unc_add(s); }
            for (int j = 0; j < ngn; j++) { int s = gain[j]; int nt = wrow[s] + 1; wrow[s] = nt; int16_t *c = &cnt[nt * n + s]; if ((*c)++ == 0 && nt == g && ssize[s] == g) unc_del(s); }
            memcpy(W, nW, sizeof W); cov = ncov; curf = nf;
            occupied[x]--; occupied[y]++; present[mask] = 0; present[moved] = 1; tick[i] = moved; accepted++;
            if (curf > bestf + 1e-15 && feasible(W, cov)) { bestf = curf; memcpy(best, tick, T * sizeof(uint32_t)); memcpy(bestW, W, sizeof W); bestcov = cov; }
        }
    }
    double secs = (double)(clock() - c0) / CLOCKS_PER_SEC;
    // verificare: recalculeaza W pentru best din zero
    uint64_t chk[8] = {0}; int chkcov = 0;
    for (int s = 0; s < n; s++) { int h = 0; for (int i = 0; i < T; i++) { int q = __builtin_popcount(smask[s] & best[i]); if (q > h) h = q; } for (int t = 1; t <= h && t <= top; t++) chk[t] += sweight[s]; if (ssize[s] == g && h >= g) chkcov++; }
    int ok = 1; for (int t = 3; t <= top; t++) if (chk[t] != bestW[t]) ok = 0; if (chkcov != bestcov) ok = 0;
    printf("best"); for (int t = 3; t <= top; t++) printf(" P%d=%.9f", t, (double)bestW[t] / total); printf(" cov=%d/%d accepted=%lld tried=%lld secs=%.1f recheck=%s\n", bestcov, nG, accepted, tried, secs, ok ? "ok" : "MISMATCH");
    FILE *o = fopen(outf, "w"); for (int i = 0; i < T; i++) { int first = 1; for (int p = 0; p < v; p++) if (best[i] >> p & 1) { fprintf(o, "%s%d", first ? "" : " ", p + 1); first = 0; } fprintf(o, "\n"); } fclose(o);
    return 0;
}
