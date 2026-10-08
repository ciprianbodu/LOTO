// Covering designs C(v, k, t) mai mici, căutate offline (recoacere simulată în
// stilul Nurmela-Östergård): fiecare t-submulțime a lui 1..v trebuie să apară
// într-un bloc de k. Mutarea ia o t-submulțime neacoperită și un bloc care o
// atinge în t-1 puncte și îi schimbă un punct; costul = t-submulțimi
// neacoperite. Temperatura coboară ciclic de la TH la TL în CYC pași. La cost 0
// designul se scrie în fișier, se scoate blocul cel mai puțin folosit și
// căutarea continuă cu un bloc mai puțin, până expiră timpul.
//
//   gcc -O2 -o cover_anneal scripts/analysis/cover_anneal.c -lm
//   ./cover_anneal v k t b seed secunde iesire.txt [start.txt]
//   (TH, TL, CYC din mediu; implicit 0.6, 0.08, 3000000)
//
// Ieșirea are formatul din covering_designs/ (poziții 1..v, un bloc pe linie).
// Un fișier intră în covering_designs/ numai după validarea exhaustivă la 100%
// (`covering.designs._load_lajolla`, `test_covering_designs.py`). Fără
// afirmație de optimalitate; așa s-au obținut C(16,6,4) și C(16,5,4) din
// 2026-10-08 (scripts/analysis/pool16_4plus_2026-10-08.md).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <stdint.h>

static int v, k, t;
static int nT;                 // number of t-sets
static int *tindex;            // mask -> t-set index (or -1)
static uint32_t *tmask;        // index -> mask
static int *cnt;               // coverage count per t-set
static uint32_t *blocks;
static int b;
static int uncovered;
static int *unc_list, *unc_pos; // uncovered set list
static uint64_t rng;

static inline uint64_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }
static inline double urand(void) { return (rnd() >> 11) * (1.0 / 9007199254740992.0); }

static void unc_add(int i) { unc_pos[i] = uncovered; unc_list[uncovered++] = i; }
static void unc_del(int i) { int p = unc_pos[i]; int last = unc_list[--uncovered]; unc_list[p] = last; unc_pos[last] = p; unc_pos[i] = -1; }

// enumerate t-subsets of mask m (popcount k) -> callback via array
static int subsets(uint32_t m, int size, uint32_t *out) {
    int pts[32], n = 0, c = 0;
    for (int i = 0; i < v; i++) if (m >> i & 1) pts[n++] = i;
    int idx[16];
    for (int i = 0; i < size; i++) idx[i] = i;
    while (1) {
        uint32_t s = 0; for (int i = 0; i < size; i++) s |= 1u << pts[idx[i]];
        out[c++] = s;
        int i = size - 1; while (i >= 0 && idx[i] == n - size + i) i--;
        if (i < 0) break;
        idx[i]++;
        for (int j = i + 1; j < size; j++) idx[j] = idx[j - 1] + 1;
    }
    return c;
}

static void add_block(uint32_t m, int sign) {
    uint32_t sub[4096]; int c = subsets(m, t, sub);
    for (int i = 0; i < c; i++) {
        int ti = tindex[sub[i]];
        if (sign > 0) { if (cnt[ti]++ == 0) unc_del(ti); }
        else { if (--cnt[ti] == 0) unc_add(ti); }
    }
}

// delta of uncovered count if block m replaces point x by y
static int delta(uint32_t m, int x, int y) {
    uint32_t base = m & ~(1u << x);          // k-1 points
    uint32_t sub[4096]; int c = subsets(base, t - 1, sub);
    int d = 0;
    for (int i = 0; i < c; i++) {
        if (cnt[tindex[sub[i] | (1u << x)]] == 1) d++;   // lost
        if (cnt[tindex[sub[i] | (1u << y)]] == 0) d--;   // gained
    }
    return d;
}

static int popc(uint32_t x) { return __builtin_popcount(x); }

int main(int argc, char **argv) {
    if (argc < 8) { fprintf(stderr, "usage: v k t b seed seconds out [start]\n"); return 2; }
    v = atoi(argv[1]); k = atoi(argv[2]); t = atoi(argv[3]); b = atoi(argv[4]);
    rng = strtoull(argv[5], 0, 10) * 2654435761ull + 88172645463325252ull;
    double secs = atof(argv[6]); const char *out = argv[7]; const char *init = argc > 8 ? argv[8] : 0; double TH = getenv("TH") ? atof(getenv("TH")) : 0.6, TL = getenv("TL") ? atof(getenv("TL")) : 0.08; long CYC = getenv("CYC") ? atol(getenv("CYC")) : 3000000;
    int full = 1 << v;
    tindex = malloc(sizeof(int) * full); tmask = malloc(sizeof(uint32_t) * full);
    nT = 0;
    for (int m = 0; m < full; m++) { tindex[m] = -1; if (popc(m) == t) { tindex[m] = nT; tmask[nT++] = m; } }
    cnt = calloc(nT, sizeof(int)); unc_list = malloc(sizeof(int) * nT); unc_pos = malloc(sizeof(int) * nT);
    blocks = malloc(sizeof(uint32_t) * 4096);
    uncovered = 0; for (int i = 0; i < nT; i++) unc_add(i);
    int nb = 0;
    if (init) { FILE *fi = fopen(init, "r"); int a; while (nb < b) { uint32_t m = 0; int ok = 1; for (int j = 0; j < k; j++) { if (fscanf(fi, "%d", &a) != 1) { ok = 0; break; } m |= 1u << (a - 1); } if (!ok) break; blocks[nb] = m; add_block(m, +1); nb++; } fclose(fi); }
    for (int i = nb; i < b; i++) {
        uint32_t m = 0; while (popc(m) < k) m |= 1u << (rnd() % v);
        blocks[i] = m; add_block(m, +1);
    }
    clock_t start = clock();
    int best_b = -1; uint32_t *best = malloc(sizeof(uint32_t) * 4096);
    double T0 = TH; double T = T0; long iter = 0; long cyc = 0;
    while ((double)(clock() - start) / CLOCKS_PER_SEC < secs) {
        if (uncovered == 0) {
            best_b = b; memcpy(best, blocks, sizeof(uint32_t) * b);
            FILE *f = fopen(out, "w");
            for (int i = 0; i < b; i++) { int first = 1; for (int p = 0; p < v; p++) if (blocks[i] >> p & 1) { fprintf(f, first ? "%d" : " %d", p + 1); first = 0; } fprintf(f, "\n"); }
            fclose(f);
            fprintf(stderr, "found b=%d after %.1fs\n", b, (double)(clock() - start) / CLOCKS_PER_SEC);
            // remove the block whose removal uncovers the fewest t-sets
            int bi = 0, bl = 1 << 30;
            for (int i = 0; i < b; i++) {
                uint32_t sub[4096]; int c = subsets(blocks[i], t, sub), l = 0;
                for (int j = 0; j < c; j++) if (cnt[tindex[sub[j]]] == 1) l++;
                if (l < bl) { bl = l; bi = i; }
            }
            add_block(blocks[bi], -1); blocks[bi] = blocks[--b];
            T = T0;
            continue;
        }
        // pick an uncovered t-set and a block meeting it in t-1 points
        int ti = unc_list[rnd() % uncovered]; uint32_t tm = tmask[ti];
        int cand[4096], nc = 0;
        for (int i = 0; i < b; i++) if (popc(blocks[i] & tm) == t - 1) cand[nc++] = i;
        int bi; int x, y;
        if (nc == 0) { bi = rnd() % b; uint32_t m = blocks[bi];
            do x = rnd() % v; while (!(m >> x & 1)); do y = rnd() % v; while (m >> y & 1);
        } else {
            bi = cand[rnd() % nc]; uint32_t m = blocks[bi];
            uint32_t yb = tm & ~m; y = __builtin_ctz(yb);
            uint32_t xs = m & ~tm; int nx = popc(xs), r = rnd() % nx;
            for (x = 0; x < v; x++) if (xs >> x & 1) { if (r-- == 0) break; }
        }
        int d = delta(blocks[bi], x, y);
        if (d <= 0 || urand() < exp(-d / T)) {
            add_block(blocks[bi], -1); blocks[bi] = (blocks[bi] & ~(1u << x)) | (1u << y); add_block(blocks[bi], +1);
        }
        iter++; if (++cyc >= CYC) cyc = 0; T = TH * pow(TL / TH, (double)cyc / CYC);
    }
    printf("%d\n", best_b);
    return 0;
}
