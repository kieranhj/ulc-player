/* IMA-style ADPCM encoder with a beam (trellis) search, for 4, 3 or 2 bits.
 *
 *   adpcm_trellis IN OUT BITS BEAM BLOCK COST [OUTTAB]
 *
 * IN     mono int16 samples (raw, little-endian)
 * OUT    per block: int16 predictor, uint8 step index, uint8 mode, then BLOCK
 *        bytes, one code each (the caller packs them)
 * BEAM   paths kept per sample (1 = best code for the next sample only)
 * COST   0: squared error of the predictor; 1: squared error of what the
 *        player outputs, OUTTAB[pred >> 4] (4096 int16: the 12-bit VIDC log
 *        table mapped back to linear), against the input, plus LAMBDA (0.25,
 *        env ADPCM_LAMBDA) times the predictor's squared error
 *
 * Decoding (must match player/adpcm_encode.py and the player's tables):
 *   step = STEPS[idx]; m = code & (half-1); diff = step >> (B-1)
 *   for each magnitude bit j (MSB first): if set, diff += step >> (B-2-j)
 *   pred = clamp16(pred +/- diff) (minus if code & half); idx = clamp(idx + INDEX_B[m], 0, 88)
 *
 * Env: ADPCM_MODES / ADPCM_COEFS (predictor modes, below), ADPCM_DECOUT=path
 *      (also write the decoded int16 samples), ADPCM_LAMBDA, ADPCM_INDEX.
 *
 * Build: zig cc -O2 -o adpcm_trellis.exe adpcm_trellis.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static const int STEPS[89] = {
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
static const int INDEX4[8] = {-1, -1, -1, -1, 2, 4, 6, 8};
static const int INDEX3[4] = {-1, -1, 2, 4};
static const int INDEX2[2] = {-1, 2};
static const int INDEX5[16] = {-1, -1, -1, -1, -1, -1, -1, -1, 1, 2, 4, 6, 8, 10, 13, 16};

static int B, HALF, NCODE;
static double LAMBDA = 0.25;          /* COST 1: weight of the predictor's own error */
static const int *INDEX;
static int DIFF[89][32], NEXT[89][32];
/* Predictor modes (env ADPCM_MODES=3, or ADPCM_COEFS for experiments): per
   block the encoder tries each mode and keeps the cheapest, storing it in the
   header's pad byte. base = y1 + (COEF[mode] * (y1 - y2)) >> 2, so with the
   default {0, 2, 4}: mode 0 is IMA (y1), 1 is y1 + (y1 - y2)/2, 2 is 2 y1 - y2.
   Each block starts with y2 = y1, so blocks still decode on their own. */
static int NMODES = 1;
static int COEF[8] = {0, 2, 4};       /* quarters: base = y1 + (COEF * (y1 - y2)) >> 2 */
static inline int base(int mode, int y1, int y2) {
    return y1 + ((COEF[mode] * (y1 - y2)) >> 2);
}

static void tables(void) {
    for (int idx = 0; idx < 89; idx++)
        for (int c = 0; c < NCODE; c++) {
            int step = STEPS[idx], m = c & (HALF - 1), d = step >> (B - 1);
            for (int j = B - 2; j >= 0; j--)
                if (m & (1 << j)) d += step >> (B - 2 - j);
            DIFF[idx][c] = (c & HALF) ? -d : d;
            int n = idx + INDEX[m];
            NEXT[idx][c] = n < 0 ? 0 : n > 88 ? 88 : n;
        }
}

typedef struct { int pred, prev, idx; double cost; int parent, code; } Node;

static int cmp(const void *a, const void *b) {
    double d = ((const Node *)a)->cost - ((const Node *)b)->cost;
    return d < 0 ? -1 : d > 0;
}

int main(int argc, char **argv) {
    if (argc < 7) { fprintf(stderr, "usage: adpcm_trellis IN OUT BITS BEAM BLOCK COST [OUTTAB]\n"); return 1; }
    B = atoi(argv[3]); int K = atoi(argv[4]), BLOCK = atoi(argv[5]), COST = atoi(argv[6]);
    HALF = 1 << (B - 1); NCODE = 1 << B;
    INDEX = B == 5 ? INDEX5 : B == 4 ? INDEX4 : B == 3 ? INDEX3 : INDEX2;
    if (getenv("ADPCM_MODES")) NMODES = atoi(getenv("ADPCM_MODES"));
    if (getenv("ADPCM_COEFS")) {                 /* "0,2,4" (quarters) */
        char *e = getenv("ADPCM_COEFS"); NMODES = 0;
        while (*e && NMODES < 8) { COEF[NMODES++] = (int)strtol(e, &e, 10); if (*e == ',') e++; }
    }
    static int custom[16];
    if (getenv("ADPCM_INDEX")) {                     /* experiments: "-1,-1,2,4" */
        char *e = getenv("ADPCM_INDEX");
        for (int i = 0; i < HALF; i++) { custom[i] = (int)strtol(e, &e, 10); if (*e == ',') e++; }
        INDEX = custom;
    }
    if (getenv("ADPCM_LAMBDA")) LAMBDA = atof(getenv("ADPCM_LAMBDA"));
    tables();
    static int16_t outtab[4096];
    if (COST == 1) {
        FILE *t = fopen(argv[7], "rb");
        if (!t || fread(outtab, 2, 4096, t) != 4096) { fprintf(stderr, "bad OUTTAB\n"); return 1; }
        fclose(t);
    }
    FILE *f = fopen(argv[1], "rb");
    fseek(f, 0, SEEK_END); long n = ftell(f) / 2; fseek(f, 0, SEEK_SET);
    long nb = (n + BLOCK - 1) / BLOCK;
    int16_t *x = calloc(nb * BLOCK, 2);
    fread(x, 2, n, f); fclose(f);
    FILE *o = fopen(argv[2], "wb");

    Node *cur = malloc(sizeof(Node) * K), *cand = malloc(sizeof(Node) * K * NCODE);
    int *bpar = malloc(sizeof(int) * BLOCK * K), *bcode = malloc(sizeof(int) * BLOCK * K);
    int pred = 0, prev = 0, idx = 0;
    unsigned char *bestcodes = malloc(BLOCK);
    FILE *dec = getenv("ADPCM_DECOUT") ? fopen(getenv("ADPCM_DECOUT"), "wb") : NULL;
    for (long b = 0; b < nb; b++) {
        int16_t *xs = x + b * BLOCK;
        double bestcost = 1e300; int bestmode = 0, bpred = 0, bprev = 0, bidx = 0;
        prev = pred;                     /* blocks are self-contained */
        for (int mode = 0; mode < NMODES; mode++) {
        int nk = 1;
        cur[0] = (Node){pred, prev, idx, 0, 0, 0};
        for (int t = 0; t < BLOCK; t++) {
            int nc = 0, s = xs[t];
            for (int k = 0; k < nk; k++)
                for (int c = 0; c < NCODE; c++) {
                    int p = base(mode, cur[k].pred, cur[k].prev) + DIFF[cur[k].idx][c];
                    p = p > 32767 ? 32767 : p < -32768 ? -32768 : p;
                    double el = (double)(p - s), e2 = el * el;
                    if (COST) {                      /* heard error, plus a little of the
                                                        predictor's own: where the log table
                                                        is coarse (near full scale) many
                                                        predictors sound alike, and a pure
                                                        heard cost lets the predictor drift
                                                        off and get stuck at the rail */
                        double eh = (double)(outtab[(p >> 4) + 2048] - s);
                        e2 = eh * eh + LAMBDA * e2;
                    }
                    cand[nc++] = (Node){p, cur[k].pred, NEXT[cur[k].idx][c], cur[k].cost + e2, k, c};
                }
            qsort(cand, nc, sizeof(Node), cmp);
            int m = 0;
            for (int i = 0; i < nc && m < K; i++) {      /* keep the K best distinct states */
                int dup = 0;
                for (int j = 0; j < m; j++)
                    if (cur[j].pred == cand[i].pred && cur[j].idx == cand[i].idx && (NMODES == 1 || cur[j].prev == cand[i].prev)) { dup = 1; break; }
                if (dup) continue;
                bpar[t * K + m] = cand[i].parent; bcode[t * K + m] = cand[i].code;
                cand[m] = cand[i];                        /* compact (i >= m) */
                cur[m] = cand[i];
                m++;
            }
            /* cur[] was overwritten in place above only after its parents were
               consumed: parents are indices into the previous cur, recorded in bpar */
            nk = m;
        }
        int k = 0;                                        /* cur[0] is the best path */
        if (getenv("ADPCM_DEBUG")) fprintf(stderr, "block %ld cost %.4g end %d %d\n", b, cur[0].cost, cur[0].pred, cur[0].idx);
        if (cur[0].cost < bestcost) {
            bestcost = cur[0].cost; bestmode = mode;
            bpred = cur[0].pred; bprev = cur[0].prev; bidx = cur[0].idx;
            for (int t = BLOCK - 1; t >= 0; t--) { bestcodes[t] = (unsigned char)bcode[t * K + k]; k = bpar[t * K + k]; }
        }
        }
        int16_t hp = (int16_t)pred; unsigned char hi[2] = {(unsigned char)idx, (unsigned char)bestmode};
        fwrite(&hp, 2, 1, o); fwrite(hi, 1, 2, o); fwrite(bestcodes, 1, BLOCK, o);
        if (dec) {                       /* replay the chosen path: the decoder's output */
            int y1 = pred, y2 = prev, ix = idx;
            for (int t = 0; t < BLOCK; t++) {
                int p = base(bestmode, y1, y2) + DIFF[ix][bestcodes[t]];
                p = p > 32767 ? 32767 : p < -32768 ? -32768 : p;
                int16_t v = (int16_t)p; fwrite(&v, 2, 1, dec);
                y2 = y1; y1 = p; ix = NEXT[ix][bestcodes[t]];
            }
        }
        pred = bpred; prev = bprev; idx = bidx;
    }
    if (dec) fclose(dec);
    fclose(o);
    return 0;
}
