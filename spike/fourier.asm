; ============================================================================
; ulc-codec IMDCT core (DCT-IV + FFT), ported to ARMv2 (ARM2/ARM250/ARM3).
;
; Started as a port of ulcplayer-gba source/libulc/Fourier_DCT4_InPlace.s and
; Fourier_FFT_InPlace.s (public domain, Ruben Nunez / Aikku93; GBA defaults
; ULC_USE_INPLACE_XFM=1, ULC_PRECISE_DCT=0, ULC_USE_64BIT_MATH=0), then
; optimised for ARM2/ARM3 (see PERF.md, "Implementation"). ARM2 and ARM3 time
; a MUL by its Rs (2 bits per cycle, no early exit for a negative Rs), and the
; ARM3's write-through cache makes every store cost ~8 cycles, so:
;  - DCT-IV pre/post rotations: 3 MULs at .12 with non-negative Rs (CMUL3);
;    the GBA's oscillators (self-modified for Log2[N]) still make the twiddles.
;  - FFT: the bit-reversal is folded into the 4-point stage, which gathers
;    from the DCT-IV's buffer into ulc_FFTBuffer (so the result lives there);
;    then the 8-point stage, radix-4 stages and at most one radix-2 stage, with
;    .FFT_TWQ twiddles from tables (no oscillators), arranged so nearly every
;    MUL's Rs is a non-negative twiddle.
; ============================================================================

; ----------------------------------------------------------------------------
; Macros
; ----------------------------------------------------------------------------

; DCT-IV oscillator, first half (k*s, k*c). The two instructions that follow
; it at each use site are patched with the Log2[N] shift.
.macro DCT_OSC_K
	add	ip, r3, r3, asr #1
	add	ip, ip, ip, asr #4
	add	lr, r2, r2, asr #2
	add	lr, lr, lr, asr #2
.endm

; FFT complex multiply, result left unscaled (.FFT_TWQ).
.macro FFT_CPLXMUL RdRe, RdIm, RmRe, RmIm, RnRe, RnIm, t0, t1, t2, t3
	mul	\t0, \RnRe, \RmRe
	mul	\t1, \RnIm, \RmRe
	mul	\t2, \RnRe, \RmIm
	mla	\RdRe, \RnIm, \RmIm, \t0
	sub	\RdIm, \t1, \t2
.endm

; Quarter-2 FFT product: Re <- b*c - a*s, Im <- a*c + b*s (unscaled),
; for (a,b) = (Re,Im) in place, (c,s) = (RmRe,RmIm).
.macro FFT_CPLXMUL_Q2 Re, Im, RmRe, RmIm, t0, t1, t2, t3
	mul	\t0, \Re, \RmRe
	mul	\t1, \Im, \RmRe
	mul	\t2, \Re, \RmIm
	mul	\t3, \Im, \RmIm
	sub	\Re, \t1, \t2
	add	\Im, \t0, \t3
.endm

; Complex multiply with two temporaries: (t0,t1) = (x*c + y*s, y*c - x*s),
; unscaled; x, y, c, s are free afterwards. c, s are the MULs' Rs.
.macro CMUL_2T x, y, c, s, t0, t1
	mul	\t0, \x, \c
	mul	\t1, \y, \s
	add	\t0, \t0, \t1
	mul	\t1, \y, \c
	mul	\y, \x, \s
	sub	\t1, \t1, \y
.endm

; DCT-IV twiddle multiply, 3 MULs at .12 (the oscillator stays .15):
;   R = (a*c + b*s) >> 15,  I = (b*c - a*s) >> 15   (NegIm: I = (a*s - b*c) >> 15)
; via k = c*(a+b), R = k - b*(c-s), I = k - a*(c+s). Every Rs is >= 0 (a
; negative c-s is made positive and the add/sub flipped): ARM2/ARM3 time a MUL
; by its Rs, and a .12 Rs is also a cycle or two cheaper than a .15 one.
; Needs R not in {a,b}, I != a, I != b; X, Y are temporaries.
.macro CMUL3 R, I, a, b, cr, sr, X, Y, NegIm
	mov	\X, \cr, asr #3
	add	\Y, \a, \b
	mul	\R, \Y, \X              ; k
	add	\Y, \X, \sr, asr #3     ; c+s
	subs	\X, \X, \sr, asr #3     ; c-s
	rsbmi	\X, \X, #0
	mul	\I, \a, \Y              ; a*(c+s)
.if \NegIm                         ; (I from k, before R is finished)
	sub	\I, \I, \R
.else
	sub	\I, \R, \I
.endif
	mul	\Y, \b, \X              ; b*|c-s|
	subpl	\R, \R, \Y
	addmi	\R, \R, \Y
	mov	\R, \R, asr #12
	mov	\I, \I, asr #12
.endm

.include "build/fourier_cfg.inc"          ; FFT_TWQ: FFT twiddle precision (bits)

; ============================================================================
; Fourier_DCT4
;  r0: &Buf (N words, .15 fixed point; used as workspace)
;  r1:  N (64..2048, power of two)
; Returns r0 = ulc_FFTBuffer, which holds the result. (The FFT reads its
; input in bit-reversed order from Buf and writes ulc_FFTBuffer, so there is
; no separate bit-reversal pass; the includer reserves 8 KB there.)
; ============================================================================

Fourier_DCT4:
	stmfd	sp!, {r4-r11,lr}
	ldr	ip, dct4_debruijn          ; Log2[N] -> ip
	adr	r3, ulc_Log2Table
	mul	r2, ip, r1
	ldrb	ip, [r3, r2, lsr #27]
	adr	r3, dct4_twiddles
	sub	r3, r3, #8*6
	add	r3, r3, ip, lsl #3
	ldmia	r3, {r2,r4}
	stmfd	sp!, {r0-r1,r4}            ; Push {Buf,N,omega1}

	adr	lr, dct4_patchops          ; Patch oscillators for Log2[N]
	ldmia	lr, {r4-r5}
	add	r4, r4, ip, lsl #7
	add	r5, r5, ip, lsl #7
	str	r4, dct4_pre_patch
	str	r5, dct4_pre_patch+4
	str	r4, dct4_post_patch
	str	r5, dct4_post_patch+4

	; Pre-rotation
	add	r1, r0, r1, lsl #2         ; &BufEnd -> r1
	mov	r3, r2, asr #16            ; omega -> r2,r3
	bic	r2, r2, r3, lsl #16
dct4_pre_loop:
	ldmia	r0, {r4,r5}                ; a0, a1
	ldmdb	r1, {r6,r7}                ; b0, b1
	CMUL3	r8, r9, r4, r7, r2, r3, ip, lr, 0
	DCT_OSC_K
dct4_pre_patch:
	sub	r2, r2, ip                 ; patched: asr #Log2[N]-1
	add	r3, r3, lr
	CMUL3	r4, r7, r6, r5, r3, r2, ip, lr, 0
	stmia	r0!, {r8,r9}
	stmdb	r1!, {r4,r7}
	cmp	r0, r1
	bcc	dct4_pre_loop

	ldmfd	sp, {r0,r1}                ; FFT(Z, N/2) -> ulc_FFTBuffer
	mov	r1, r1, lsr #1
	bl	Fourier_FFT

	; Post-rotation, in place in ulc_FFTBuffer
	ldmfd	sp!, {r0-r2}               ; {Buf,N,omega1}
	ldr	r0, p_fftbuf
	add	r1, r0, r1, lsl #2
	mov	r3, r2, asr #16
	bic	r2, r2, r3, lsl #16
dct4_post_loop:
	ldmia	r0, {r4,r5}
	ldmdb	r1, {r6,r7}
	CMUL3	r8, r11, r4, r5, r2, r3, ip, lr, 1
	DCT_OSC_K
dct4_post_patch:
	sub	r2, r2, ip                 ; patched
	add	r3, r3, lr
	CMUL3	r9, r10, r6, r7, r3, r2, r4, r5, 1
	stmia	r0!, {r8,r10}
	stmdb	r1!, {r9,r11}
	cmp	r0, r1
	bcc	dct4_post_loop
	ldr	r0, p_fftbuf
	ldmfd	sp!, {r4-r11,pc}

p_fftbuf:
	.long	ulc_FFTBuffer

dct4_patchops:
	.long	0xE0421FCC                 ; SUB r2, r2, ip, asr #-1 (+Log2[N]<<7)
	.long	0xE0832FCE                 ; ADD r3, r3, lr, asr #-1
dct4_debruijn:
	.long	0x077CB531

; 2^15*{E^(I*Pi*(0 + 1/2)/N), E^(I*Pi*(0 - 1/4)/N)}
dct4_twiddles:
	.short	0x7FF6, 0x0324, 0x7FFE, -0x0192 ; N = 64
	.short	0x7FFE, 0x0192, 0x7FFF, -0x00C9 ; N = 128
	.short	0x7FFF, 0x00C9, 0x8000, -0x0065 ; N = 256
	.short	0x8000, 0x0065, 0x8000, -0x0032 ; N = 512
	.short	0x8000, 0x0032, 0x8000, -0x0019 ; N = 1024
	.short	0x8000, 0x0019, 0x8000, -0x000D ; N = 2048

ulc_Log2Table:
	.byte	 0, 1,28, 2,29,14,24, 3
	.byte	30,22,20,15,25,17, 4, 8
	.byte	31,27,13,23,21,19,16, 7
	.byte	26,12,18, 6,11, 5,10, 9

; ============================================================================
; Fourier_FFT
;  r0: &Src (N complex values, Re/Im interleaved words; natural order)
;  r1:  N (16..1024)
; Output (natural order) in ulc_FFTBuffer. The bit-reversal permutation is
; folded into the 4-point stage, which gathers its inputs from Src.
; ============================================================================

Fourier_FFT:
	stmfd	sp!, {r4-r11,lr}
	ldr	r2, dct4_debruijn          ; Log2[N]
	mul	r3, r2, r1
	adr	r2, ulc_Log2Table
	ldrb	r3, [r2, r3, lsr #27]
	adr	r2, fft_br                 ; index by Log2[N], then the tables
	ldr	ip, [r2, r3, lsl #2]
	add	ip, r2, ip                 ; &BitRev[N/4]: byte offsets 8*BitRev(j)
	mov	r11, r0                    ; &Src
	ldr	r0, p_fftbuf
	add	r10, r0, r1, lsl #3        ; &BufEnd -> sl
	str	r1, [sp, #-4]!
	mov	r1, r1, lsl #1             ; N/4 complex = 2N bytes
	; 4-point stage: block j takes Src[b], Src[b+N/2], Src[b+N/4], Src[b+3N/4]
	; for b = BitRev_{N/4}(j)
fft4_loop:
	ldr	lr, [ip], #4
	add	lr, r11, lr
	ldmia	lr, {r2-r3}
	add	lr, lr, r1
	ldmia	lr, {r6-r7}
	add	lr, lr, r1
	ldmia	lr, {r4-r5}
	add	lr, lr, r1
	ldmia	lr, {r8-r9}
	add	r2, r2, r4
	add	r3, r3, r5
	sub	r4, r2, r4, lsl #1
	sub	r5, r3, r5, lsl #1
	add	r6, r6, r8
	add	r7, r7, r9
	sub	r8, r6, r8, lsl #1
	sub	r9, r7, r9, lsl #1
	add	r2, r2, r6
	add	r3, r3, r7
	sub	r6, r2, r6, lsl #1
	sub	r7, r3, r7, lsl #1
	add	r4, r4, r9
	sub	r5, r5, r8
	sub	lr, r4, r9, lsl #1
	add	r9, r5, r8, lsl #1
	mov	r8, lr
	stmia	r0!, {r2-r9}
	cmp	r0, r10
	bcc	fft4_loop
	ldr	r1, [sp], #4

	; 8-point stage. Sqrt[1/2] is the MUL's Rs (ARM2/ARM3 time a MUL by its
	; Rs: a constant 14-bit one is cheaper than a data value, often negative).
	sub	r0, r0, r1, lsl #3
	str	r1, [sp, #-4]!             ; free r1
	mov	r2, #0x41                  ; Sqrt[1/2] .14
	orr	r2, r2, #0x2D00
fft8_loop:
	add	r3, r0, #8*4
	ldmia	r3, {r8,r9,ip,lr}
	add	ip, ip, lr
	rsb	lr, ip, lr, lsl #1
	mul	r1, ip, r2
	mul	ip, lr, r2
	ldmia	r0, {r4,r5,r6,r7}
	add	r4, r4, r8
	add	r5, r5, r9
	sub	r8, r4, r8, lsl #1
	sub	r9, r5, r9, lsl #1
	add	r6, r6, r1, asr #14
	add	r7, r7, ip, asr #14
	sub	lr, r7, ip, asr #13
	sub	ip, r6, r1, asr #13
	stmia	r0!, {r4,r5,r6,r7}
	stmia	r3!, {r8,r9,ip,lr}
	ldmia	r3, {r8,r9,ip,lr}
	rsb	ip, ip, lr
	sub	lr, ip, lr, lsl #1
	mul	r1, ip, r2
	mul	ip, lr, r2
	ldmia	r0, {r4,r5,r6,r7}
	add	r4, r4, r9
	sub	r5, r5, r8
	add	r11, r5, r8, lsl #1
	sub	r8, r4, r9, lsl #1
	add	r6, r6, r1, asr #14
	add	r7, r7, ip, asr #14
	sub	lr, r7, ip, asr #13
	sub	ip, r6, r1, asr #13
	stmia	r0!, {r4,r5,r6,r7}
	stmia	r3!, {r8,r11,ip,lr}
	add	r0, r0, #8*4
	cmp	r0, r10
	bcc	fft8_loop
	ldr	r1, [sp], #4

	; Radix-4 stages: L -> 4L for L = 8, 32, 128 while 4L <= N, then one
	; radix-2 stage (M = N) if N/8 is an odd power of two. A 4L block holds the
	; L-point DFTs of x[4n], x[4n+2], x[4n+1], x[4n+3] (bit-reversed order), so
	; for k < L, with W = E^(-2Pi*I/4L):
	;   a = Buf[k], b = W^k Buf[k+2L], c = W^2k Buf[k+L], d = W^3k Buf[k+3L]
	;   X[k] = a+b+c+d, X[k+L] = a-ib-c+id, X[k+2L] = a-b+c-d, X[k+3L] = a+ib-c-id
	; fft_r4tw holds, per stage and k, w2, w1, w3 (.FFT_TWQ). For k >= L/2 it
	; holds W^2k * i and -W^3k instead, which keeps the angles (and so the
	; MULs' Rs) mostly in the first quadrant; the second loop undoes them.
	;   r10 &Buf[k]  r11 S = 8L bytes  r12 twiddles  lr count  r0-r9 data
	adr	r12, fft_r4tw
	mov	r11, #8*8
	stmfd	sp!, {r1, r10, r12}        ; {N, &BufEnd, &StageTwiddles}
fft4r_stage:
	ldmia	sp, {r1, r10}
	cmp	r11, r1, lsl #1            ; 4L > N: no more radix-4 stages
	bhi	fft4r_done
	sub	r10, r10, r1, lsl #3
fft4r_block:
	ldr	r12, [sp, #8]
	mov	lr, r11, lsr #4            ; L/2
fft4r_h1:
	add	r9, r10, r11               ; q1 = Buf[k+L]
	ldmia	r9, {r0, r1}
	ldmia	r12!, {r2, r3}             ; w2
	CMUL_2T	r0, r1, r2, r3, r4, r5     ; C
	add	r9, r10, r11, lsl #1       ; q2 = Buf[k+2L]
	ldmia	r9, {r0, r1}
	ldmia	r12!, {r2, r3}             ; w1
	CMUL_2T	r0, r1, r2, r3, r6, r7     ; B
	add	r9, r10, r11               ; q3 = Buf[k+3L]
	add	r9, r9, r11, lsl #1
	ldmia	r9, {r0, r1}
	ldmia	r12!, {r2, r3}             ; w3
	CMUL_2T	r0, r1, r2, r3, r8, r9     ; D
	mov	r6, r6, asr #FFT_TWQ
	mov	r7, r7, asr #FFT_TWQ
	add	r6, r6, r8, asr #FFT_TWQ   ; t2 = b + d
	add	r7, r7, r9, asr #FFT_TWQ
	sub	r8, r6, r8, asr #(FFT_TWQ-1) ; t3 = b - d
	sub	r9, r7, r9, asr #(FFT_TWQ-1)
	ldmia	r10, {r0, r1}              ; a
	add	r0, r0, r4, asr #FFT_TWQ   ; t0 = a + c
	add	r1, r1, r5, asr #FFT_TWQ
	sub	r4, r0, r4, asr #(FFT_TWQ-1) ; t1 = a - c
	sub	r5, r1, r5, asr #(FFT_TWQ-1)
	add	r0, r0, r6                 ; X[k]    = t0 + t2
	add	r1, r1, r7
	sub	r6, r0, r6, lsl #1         ; X[k+2L] = t0 - t2
	sub	r7, r1, r7, lsl #1
	add	r2, r4, r9                 ; X[k+L]  = t1 - i*t3
	sub	r3, r5, r8
	sub	r4, r4, r9                 ; X[k+3L] = t1 + i*t3
	add	r5, r5, r8
	add	r8, r10, r11
	stmia	r8, {r2, r3}               ; X[k+L]
	add	r8, r8, r11
	stmia	r8, {r6, r7}               ; X[k+2L]
	add	r8, r8, r11
	stmia	r8, {r4, r5}               ; X[k+3L]
	stmia	r10!, {r0, r1}             ; X[k], next k
	subs	lr, lr, #1
	bne	fft4r_h1
	mov	lr, r11, lsr #4
fft4r_h2:                              ; c = -i*C, d = -D
	add	r9, r10, r11               ; q1 = Buf[k+L]
	ldmia	r9, {r0, r1}
	ldmia	r12!, {r2, r3}             ; w2
	CMUL_2T	r0, r1, r2, r3, r4, r5     ; C
	add	r9, r10, r11, lsl #1       ; q2 = Buf[k+2L]
	ldmia	r9, {r0, r1}
	ldmia	r12!, {r2, r3}             ; w1
	CMUL_2T	r0, r1, r2, r3, r6, r7     ; B
	add	r9, r10, r11               ; q3 = Buf[k+3L]
	add	r9, r9, r11, lsl #1
	ldmia	r9, {r0, r1}
	ldmia	r12!, {r2, r3}             ; w3
	CMUL_2T	r0, r1, r2, r3, r8, r9     ; D
	mov	r6, r6, asr #FFT_TWQ
	mov	r7, r7, asr #FFT_TWQ
	sub	r6, r6, r8, asr #FFT_TWQ   ; t2 = b + d = B - D
	sub	r7, r7, r9, asr #FFT_TWQ
	add	r8, r6, r8, asr #(FFT_TWQ-1) ; t3 = b - d = B + D
	add	r9, r7, r9, asr #(FFT_TWQ-1)
	ldmia	r10, {r0, r1}              ; a
	add	r0, r0, r5, asr #FFT_TWQ   ; t0 = a + c = (ar + Ci, ai - Cr)
	sub	r1, r1, r4, asr #FFT_TWQ
	sub	r2, r0, r5, asr #(FFT_TWQ-1) ; t1 = a - c = (ar - Ci, ai + Cr)
	add	r5, r1, r4, asr #(FFT_TWQ-1)
	sub	r4, r2, r9                 ; X[k+3L] = t1 + i*t3
	add	r2, r2, r9                 ; X[k+L]  = t1 - i*t3
	sub	r3, r5, r8
	add	r5, r5, r8
	add	r0, r0, r6                 ; X[k]    = t0 + t2
	add	r1, r1, r7
	sub	r6, r0, r6, lsl #1         ; X[k+2L] = t0 - t2
	sub	r7, r1, r7, lsl #1
	add	r8, r10, r11
	stmia	r8, {r2, r3}               ; X[k+L]
	add	r8, r8, r11
	stmia	r8, {r6, r7}               ; X[k+2L]
	add	r8, r8, r11
	stmia	r8, {r4, r5}               ; X[k+3L]
	stmia	r10!, {r0, r1}             ; X[k], next k
	subs	lr, lr, #1
	bne	fft4r_h2
	add	r10, r10, r11              ; next block: skip Buf[L..4L)
	add	r10, r10, r11, lsl #1
	ldr	r0, [sp, #4]
	cmp	r10, r0
	bcc	fft4r_block
	ldr	r12, [sp, #8]              ; next stage: L*24 bytes of twiddles = 3S
	add	r12, r12, r11
	add	r12, r12, r11, lsl #1
	str	r12, [sp, #8]
	mov	r11, r11, lsl #2
	b	fft4r_stage
fft4r_done:
	cmp	r11, r1, lsl #3            ; L == N: done
	addeq	sp, sp, #12
	ldmeqfd	sp!, {r4-r11,pc}
	adr	ip, fft_tw - 32            ; radix-2 stage M = N: its twiddles start
	add	ip, ip, r1, lsl #1         ;  2*(N-16) bytes into fft_tw
	str	ip, [sp, #8]
	mov	r11, r1
	b	fftn_stage

	; Radix-2 stage, M = 16..N (only ever the last, M = N, now), twiddles from fft_tw.
	; fft_tw holds, per stage, w(k) = cos + i*sin(2*Pi*k/M) for k < M/4 only:
	; both parts are >= 0, so every MUL has a non-negative Rs (a negative Rs
	; costs ARM2/ARM3 ~8 cycles more). The second quarter uses
	; w(k + M/4) = -i * w(k), i.e. the same products with swapped add/sub.
	; Each block: top half r0, bottom half r1 (= r0 + M/2), 2 butterflies per
	; iteration. r11 = M | -iterations<<16.
fftn_stage:
	ldmia	sp, {r1, r10}
	sub	r0, r10, r1, lsl #3
fftn_block:
	add	r1, r0, r11, lsl #2        ; &Buf[M/2]
	ldr	r10, [sp, #8]
	sub	r11, r11, r11, lsl #13     ; M/8 iterations
fftn_q1:
	ldmia	r1, {r8,r9,ip,lr}
	ldmia	r10!, {r2,r3}
	FFT_CPLXMUL r8,r9, r2,r3, r8,r9, r4,r5,r6,r7
	ldmia	r10!, {r2,r3}
	FFT_CPLXMUL ip,lr, r2,r3, ip,lr, r4,r5,r6,r7
	ldmia	r0, {r4,r5,r6,r7}
	add	r4, r4, r8, asr #(FFT_TWQ)
	add	r5, r5, r9, asr #(FFT_TWQ)
	add	r6, r6, ip, asr #(FFT_TWQ)
	add	r7, r7, lr, asr #(FFT_TWQ)
	sub	r8, r4, r8, asr #(FFT_TWQ-1)
	sub	r9, r5, r9, asr #(FFT_TWQ-1)
	sub	ip, r6, ip, asr #(FFT_TWQ-1)
	sub	lr, r7, lr, asr #(FFT_TWQ-1)
	stmia	r0!, {r4,r5,r6,r7}
	stmia	r1!, {r8,r9,ip,lr}
	adds	r11, r11, #1<<16
	bcc	fftn_q1
	ldr	r10, [sp, #8]
	sub	r11, r11, r11, lsl #13
fftn_q2:                               ; x*w(k+M/4) = (Q, -P), P,Q as in quarter 1
	ldmia	r1, {r8,r9,ip,lr}
	ldmia	r10!, {r2,r3}
	FFT_CPLXMUL_Q2 r8,r9, r2,r3, r4,r5,r6,r7
	ldmia	r10!, {r2,r3}
	FFT_CPLXMUL_Q2 ip,lr, r2,r3, r4,r5,r6,r7
	ldmia	r0, {r4,r5,r6,r7}
	add	r4, r4, r8, asr #(FFT_TWQ)
	sub	r5, r5, r9, asr #(FFT_TWQ)
	add	r6, r6, ip, asr #(FFT_TWQ)
	sub	r7, r7, lr, asr #(FFT_TWQ)
	sub	r8, r4, r8, asr #(FFT_TWQ-1)
	add	r9, r5, r9, asr #(FFT_TWQ-1)
	sub	ip, r6, ip, asr #(FFT_TWQ-1)
	add	lr, r7, lr, asr #(FFT_TWQ-1)
	stmia	r0!, {r4,r5,r6,r7}
	stmia	r1!, {r8,r9,ip,lr}
	adds	r11, r11, #1<<16
	bcc	fftn_q2
	mov	r0, r1                     ; next block
	ldr	r10, [sp, #4]
	cmp	r0, r10
	bcc	fftn_block
	ldr	r10, [sp, #8]              ; next stage
	add	r10, r10, r11, lsl #1      ; M/4 twiddles of 8 bytes
	str	r10, [sp, #8]
	ldr	r1, [sp]
	mov	r11, r11, lsl #1
	cmp	r11, r1
	bls	fftn_stage
	add	sp, sp, #12
	ldmfd	sp!, {r4-r11,pc}

.align 4
fft_tw:
	.incbin	"build/fft_tw.bin"         ; written by ulc_tools.write_fourier_tables
fft_br:
	.incbin	"build/fft_br.bin"         ; index by Log2[N], then 8*BitRev_{N/4}(j) tables
fft_r4tw:
	.incbin	"build/fft_r4tw.bin"       ; radix-4 twiddles, see fft4r_stage
