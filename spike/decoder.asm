; ============================================================================
; ulc-codec block decoder, ported to ARMv2 (ARM2/ARM250/ARM3).
;
; Port of the decode path of ulcplayer-gba source/libulc/ulc_UpdatePlayer.s
; (public domain, Ruben Nunez / Aikku93) with the GBA defaults
; (COEF_PRECISION=15, INPLACE_XFM=1, 64BIT_MATH=0, STEREO_SUPPORT=1).
;
; ulc_DecodeBlock decodes ONE block (all channels) from ulc_State:
;   [0] &NextData       updated on return
;   [4]  LastSubBlockSize   updated on return
;   [8]  BlockSize
;   [12] nChan (1 or 2)
;   [16] &OutBuf        BlockSize bytes per channel; channel 2 at +CH2_OFFS
;   [20] &LapTable      centre of a 2 KB byte table: output = LapTable[x], x the
;                       sample clipped to 11 bits (-1024..1023). A VIDC log table
;                       lets mono write VIDC bytes straight to the sound ring.
;   [24] &MSDest        stereo only: L,R interleaved output (2*BlockSize bytes)
;   [28] &MSTable       stereo only: 256-byte table applied to the 8-bit L and R
;                       (stereo's LapTable must give 8-bit linear for the M/S step)
; Returns r0 = bytes consumed.
;
; Changes from the GBA original:
;  - Buffer/pause/DMA bookkeeping (nBufProc, WrBufIdx, nBlkRem, IRQ lock with
;    MRS/MSR) dropped: the caller decides when to decode and where to.
;  - LDRH/STRH -> word fields; the self-modifying dequantiser is patched with a
;    whole-word STR instead of STRH of its low half.
;  - MUL ip,ip,ip (Rd==Rm is illegal on ARMv2) goes through r1.
;  - UMULL in the tail noise-fill -> two 16x16 MULs, bit-exact for the
;    15-bit amplitude it is used with.
;  - CH2_OFFS replaces MAX_BLOCK_SIZE*2 so a harness can keep many blocks.
;  - Output: the sample is clipped to 11 bits and looked up in LapTable (the
;    GBA clips to 8 bits); stereo's M/S step also converts and interleaves.
;  - Speed (PERF.md): coefficient nybbles dispatch with one table load
;    (dc_table) instead of a compare chain; the window rotation takes 3 MULs
;    at .12 with non-negative Rs (OV_ROT); the DCT-IV leaves its result in
;    ulc_FFTBuffer, which the includer reserves (8 KB).
; Everything else - including the approximate clip, the oscillator patching
; and the lapping - is the original algorithm.
; ============================================================================

.equ ULC_COEF_PRECISION, 15
.equ ULC_MAX_BLOCK_SIZE, 2048
.ifndef CH2_OFFS
.equ CH2_OFFS, 0x20000
.endif
.equ LAPPING_BUFFER_OFFS, 4*ULC_MAX_BLOCK_SIZE

.equ QSCALE_BASE, (0x5045 | ((0x18+5-ULC_COEF_PRECISION)<<7))  ; MOV r5,r5,ASR #x (low half)
.equ QSCALE_ZERO, 0x5025                                     ; MOV r5,r5,LSR #32 (low half)

.macro NextNybble
	adds	r6, r6, #1<<29
	ldrcs	r7, [r6, #4]!
	movcc	r7, r7, lsr #4
.endm

.macro STEP_OSCILLATOR PatchLabel
	add	ip, r9, r9, asr #1
	add	ip, ip, ip, asr #4
	add	lr, r8, r8, asr #2
	add	lr, lr, lr, asr #2
\PatchLabel:
	sub	r8, r8, ip                 ; patched: asr #Log2[OverlapSize]
	add	r9, r9, lr
.endm

; Window rotation of one (lo, hi) pair: lo <- (lo*c - hi*s) >> 15,
; Rhi <- (lo*s + hi*c) >> 15, with k = s*(lo+hi): Rhi = k - hi*(s-c),
; lo = lo*(c+s) - k. MULs at .12, every Rs >= 0. Uses ip, lr.
.macro OV_ROT Rlo, Rhi_in, Rhi
	mov	ip, r9, asr #3
	add	lr, \Rlo, \Rhi_in
	mul	\Rhi, lr, ip              ; k
	add	lr, ip, r8, asr #3         ; c+s
	subs	ip, ip, r8, asr #3         ; s-c
	rsbmi	ip, ip, #0
	mul	ip, \Rhi_in, ip           ; hi*|s-c|
	mul	lr, \Rlo, lr              ; lo*(c+s)
	sub	\Rlo, lr, \Rhi
	subpl	\Rhi, \Rhi, ip
	addmi	\Rhi, \Rhi, ip
	mov	\Rhi, \Rhi, asr #12
	mov	\Rlo, \Rlo, asr #12
.endm

.macro MS_PAIR Racc, Sh                ; one L,R pair from the low bytes of r0 (M), r1 (S)
	mov	ip, r0, lsl #24
	adds	r2, ip, r1, lsl #24
	addvs	r2, r7, r2, asr #31        ; saturate
	subs	r3, ip, r1, lsl #24
	addvs	r3, r7, r3, asr #31
	ldrb	r2, [lr, r2, lsr #24]
	ldrb	r3, [lr, r3, lsr #24]
	mov	r0, r0, lsr #8
	mov	r1, r1, lsr #8
.if (\Sh == 0)
	orr	\Racc, r2, r3, lsl #8
.else
	orr	\Racc, \Racc, r2, lsl #16
	orr	\Racc, \Racc, r3, lsl #24
.endif
.endm

.macro CLIP_LOOKUP Rm, Rn, Rt
	mov	\Rm, \Rm, asr #(ULC_COEF_PRECISION-10)
	teq	\Rm, \Rm, lsl #21        ; same approximate clip as the GBA's, at 11 bits
	eormi	\Rm, \Rn, \Rm, asr #32
	ldrb	\Rm, [\Rt, \Rm]
.endm

; ============================================================================

ulc_DecodeBlock:
	stmfd	sp!, {r4-r11,lr}
	adr	r4, ulc_State
	ldr	r6, [r4, #0]               ; &NextData
	ldr	fp, [r4, #8]               ; BlockSize
	ldr	r2, [r4, #12]              ; nChan
	ldr	r3, [r4, #16]              ; &OutBuf
	and	ip, r6, #3                 ; prepare nybble reader
	ldr	r7, [r6, -ip]!
	orr	r6, r6, ip, lsl #30
	movs	ip, ip, lsl #3
	movne	r7, r7, lsr ip
	orr	r3, r3, r2, lsr #1         ; IsStereo flag in bit 0
	ldr	r4, [r4, #4]               ; LastSubBlockSize
	mov	r9, #QSCALE_BASE & 0xFF00
	orr	r9, r9, #QSCALE_BASE & 0xFF
	str	r3, [sp, #-4]!

	and	r5, r7, #0x0F              ; block header: QScaleBase | WindowCtrl<<16 -> r9
	orr	r9, r9, r5, lsl #16
	NextNybble
	tst	r5, #8
	beq	hdr_done
	orr	r9, r9, r7, lsl #28        ; decimation nybble in top bits
	NextNybble
hdr_done:

chan_loop:
	adr	r8, decimation_patterns
	ldr	r8, [r8, r9, lsr #28-2]

subblock_loop:
	mov	r0, #0
	mov	r1, #0
	mov	r2, #0
	mov	r3, #0
	and	r5, r8, #7
	mov	r5, fp, lsr r5             ; SubBlockSize
	orr	r4, r4, r5, lsl #16        ; LastSubBlockSize | SubBlockSize<<16
	sub	fp, fp, r5, lsl #16        ; BlockSize | -CoefRem<<16
	ldr	sl, p_transform
	b	dc_change_quant

decimation_patterns:
	.long	(0+8)
	.long	0
	.long	(1+8) | (1  )<<4
	.long	(1  ) | (1+8)<<4
	.long	(2+8) | (2  )<<4 | (1  )<<8
	.long	(2  ) | (2+8)<<4 | (1  )<<8
	.long	(1  ) | (2+8)<<4 | (2  )<<8
	.long	(1  ) | (2  )<<4 | (2+8)<<8
	.long	(3+8) | (3  )<<4 | (2  )<<8 | (1  )<<12
	.long	(3  ) | (3+8)<<4 | (2  )<<8 | (1  )<<12
	.long	(2  ) | (3+8)<<4 | (3  )<<8 | (1  )<<12
	.long	(2  ) | (3  )<<4 | (3+8)<<8 | (1  )<<12
	.long	(1  ) | (3+8)<<4 | (3  )<<8 | (2  )<<12
	.long	(1  ) | (3  )<<4 | (3+8)<<8 | (2  )<<12
	.long	(1  ) | (2  )<<4 | (3+8)<<8 | (3  )<<12
	.long	(1  ) | (2  )<<4 | (3  )<<8 | (3+8)<<12

p_transform:
	.long	ulc_TransformBuffer

; ----------------------------------------------------------------------------
; Coefficient unpacking
; r0-r3: 0   r4: LastSubBlockSize|SubBlockSize<<16   r6/r7: reader
; r8: DecimationPattern   r9: QScaleBase|WindowCtrl<<16   sl: &CoefDst
; fp: BlockSize | -CoefRem<<16   lr: Log2[Quant]
; ----------------------------------------------------------------------------

dc_escape:
	NextNybble
dc_change_quant:
	and	r5, r7, #0x0F
	NextNybble
	cmp	r5, #0x0E
	bhi	dc_stop_noisefill          ; Fh,Fh,Zh,Yh,Xh
	movcc	lr, r5
	bcc	dc_patch
	and	r5, r7, #0x0F
	NextNybble
	cmp	r5, #0x0F                  ; Fh,Eh,Fh: stop
	subeq	r5, r0, fp, asr #16
	beq	dc_zeros_core
	add	lr, r5, #0x0E              ; extended-precision quantiser
	cmp	lr, #0x20-24-5+ULC_COEF_PRECISION
	eorcs	r5, r9, #QSCALE_BASE ^ QSCALE_ZERO
dc_patch:
	addcc	r5, r9, lr, lsl #7
	mov	r5, r5, lsl #16            ; build the whole MOV instruction word
	mov	r5, r5, lsr #16
	orr	r5, r5, #0xE1000000
	orr	r5, r5, #0x00A00000
	str	r5, dc_shifter

dc_loop_ip:
	adr	ip, dc_table               ; (the escape handlers use ip)
dc_loop:                               ; one table load per nybble: the coefficient,
	and	r5, r7, #0x0F              ; or for 0h/1h/8h/Fh its handler's address + 1
	ldr	r5, [ip, r5, lsl #2]       ; (the GBA used a compare chain)
	tst	r5, #1
	subne	pc, r5, #1
	NextNybble
dc_shifter:
	mov	r5, r5, asr #0             ; patched dequantiser
	str	r5, [sl], #4
	adds	fp, fp, #1<<16
	bcc	dc_loop
	b	dc_done

dc_table:                              ; by nybble (the GBA's negated tonal coefficients)
	.long	dc_zeros + 1, dc_zeros_long + 1
	.long	-0x04000000,-0x09000000,-0x10000000,-0x19000000,-0x24000000,-0x31000000
	.long	dc_noisefill + 1
	.long	+0x31000000,+0x24000000,+0x19000000,+0x10000000,+0x09000000,+0x04000000
	.long	dc_escape + 1

dc_noisefill:
	NextNybble
	and	r5, r7, #0x0F
	NextNybble
	and	ip, r7, #0x0F
	NextNybble
	orr	r5, ip, r5, lsl #4
	and	ip, r7, #0x0F
	NextNybble
	movs	ip, ip, lsr #1
	adc	r5, r5, r5
	add	ip, ip, #1
	mul	r1, ip, ip                 ; ARMv2: Rd may not equal Rm
	add	r5, r5, #0x10
	mov	ip, r1, lsl #ULC_COEF_PRECISION-5-2
	mov	r1, #0
	movs	ip, ip, lsr lr
	adcs	ip, ip, #0
	beq	dc_zeros_core
	add	fp, fp, r5, lsl #16
	sub	lr, lr, r5, lsl #8
	eor	r5, r6, r7, ror #23        ; seed
dc_nf_loop:
	eor	r5, r5, r5, lsl #13
	eor	r5, r5, r5, lsr #17
	eors	r5, r5, r5, lsl #5
	rsbmi	ip, ip, #0
	adds	lr, lr, #1<<8
	str	ip, [sl], #4
	bcc	dc_nf_loop
	cmp	fp, #0x10000
	bcs	dc_loop_ip
	b	dc_done

dc_zeros:
	NextNybble
	and	r5, r7, #0x0F
	NextNybble
	add	r5, r5, #1
dc_zeros_core:
	add	fp, fp, r5, lsl #16
	movs	ip, r5, lsl #31
	strmi	r0, [sl], #4
	stmcsia	sl!, {r0-r1}
	movs	r5, r5, lsr #2
dc_zeros_4:
	stmneia	sl!, {r0-r3}
	subnes	r5, r5, #1
	bne	dc_zeros_4
	cmp	fp, #0x10000
	bcs	dc_loop_ip
	b	dc_done

dc_zeros_long:
	NextNybble
	and	r5, r7, #0x0F
	NextNybble
	and	ip, r7, #0x0F
	NextNybble
	orr	r5, ip, r5, lsl #4
	add	r5, r5, #0x21
	b	dc_zeros_core

dc_stop_noisefill:
	and	ip, r7, #0x0F              ; v
	NextNybble
	and	r5, r7, #0x0F              ; r
	NextNybble
	orr	r5, r5, r7, lsl #28
	mov	r5, r5, ror #28
	NextNybble
	add	ip, ip, #1
	mul	r1, ip, ip
	mul	r2, r5, r5
	mov	r1, r1, lsl #ULC_COEF_PRECISION-5-4   ; Scale (< 2^15)
	subs	r2, r0, r2, lsl #32-19     ; Decay (0.32)
	mvneq	r2, #0
	eor	r5, r6, r7, ror #23        ; seed
	mov	r0, r2, lsr #16            ; Decay.hi -> r0
	bic	r2, r2, r0, lsl #16        ; Decay.lo -> r2
dc_snf_loop:
	eor	r5, r5, r5, lsl #13
	eor	r5, r5, r5, lsr #17
	eors	r5, r5, r5, lsl #5
	mov	r3, r1, lsr lr
	rsbmi	r3, r3, #0
	str	r3, [sl], #4
	mul	r3, r0, r1                 ; Scale = (Scale*Decay) >> 32, exact:
	mul	ip, r2, r1                 ;  (hi*S + (lo*S >> 16)) >> 16
	add	r3, r3, ip, lsr #16
	mov	r1, r3, lsr #16
	adds	fp, fp, #1<<16
	bcc	dc_snf_loop

dc_done:
	sub	sl, sl, r4, lsr #16-2      ; rewind to start of coefficients

; ----------------------------------------------------------------------------
; IMDCT, windowing and lapping
; ----------------------------------------------------------------------------

	mov	r0, sl
	mov	r1, r4, lsr #16
	bl	Fourier_DCT4
	add	r5, sl, #LAPPING_BUFFER_OFFS
	mov	sl, r0                     ; the DCT-IV leaves its result in ulc_FFTBuffer
	stmfd	sp!, {r6-r9}
	mov	r6, r4, lsr #16            ; OverlapSize = SubBlockSize
	bic	r7, r4, r6, lsl #16        ; LastSubBlockSize
	tst	r8, #8
	movne	ip, r9, lsr #16
	andne	ip, ip, #7
	movne	r6, r6, lsr ip
	cmp	r6, r7
	movhi	r6, r7
	ldr	r7, [sp, #0x10]            ; &OutBuf
	mov	r4, r4, lsr #16
	add	ip, r7, r4
	str	ip, [sp, #0x10]
	tst	r7, #0x80000000            ; second channel?
	bic	r7, r7, #0x80000001
	addne	r7, r7, #CH2_OFFS
	addne	r5, r5, #4*ULC_MAX_BLOCK_SIZE/2
	stmfd	sp!, {r7,fp}

	add	ip, sl, r4, lsl #1         ; swap lapped half
	add	lr, r5, r4, lsl #1
	orr	r4, r4, r6, lsl #16
swap_loop:
	ldmia	r5, {r0-r1}
	ldmdb	lr, {r2-r3}
	ldmia	sl, {r6-r7}
	ldmdb	ip, {r8-r9}
	str	r0, [ip, #-4]!
	str	r1, [ip, #-4]!
	str	r3, [sl], #4
	str	r2, [sl], #4
	stmia	r5!, {r6-r7}
	stmdb	lr!, {r8-r9}
	cmp	sl, ip
	bcc	swap_loop

	movs	r0, r4, lsr #16            ; OverlapSize?
	bic	r4, r4, r0, lsl #16
	sub	sl, sl, r4
	sub	r5, r5, r4
	beq	skip_overlap
	ldr	r2, ov_debruijn
	adr	r3, ulc_Log2Table
	mul	r1, r2, r0
	ldrb	r1, [r3, r1, lsr #27]      ; Log2[OverlapSize]
	adr	r8, ov_twiddles - 4*4
	ldr	r2, ov_patchops
	ldr	r3, ov_patchops+4
	ldr	r8, [r8, r1, lsl #2]       ; omega
	add	r2, r2, r1, lsl #7
	add	r3, r3, r1, lsl #7
	str	r2, ov_patch0
	str	r3, ov_patch0+4
	str	r2, ov_patch1
	str	r3, ov_patch1+4
	mov	r9, r8, lsr #16            ; s
	bic	r8, r8, r9, lsl #16        ; c
	sub	r6, r4, r0
	add	sl, sl, r6, lsl #1         ; BufLo
	add	fp, sl, r4, lsl #1         ; BufHi
	sub	r4, r4, r0, lsl #15        ; N | -OverlapSize/2<<16
ov_loop:                               ; (lo,hi) -> (lo*c - hi*s, lo*s + hi*c)
	ldmia	sl, {r0-r1}                ; for the window (c,s) = (r8,r9), with
	ldmia	fp, {r2-r3}                ; 3 MULs as in CMUL3 (fourier.asm)
	OV_ROT	r0, r2, r6
	STEP_OSCILLATOR ov_patch0
	OV_ROT	r1, r3, r7
	STEP_OSCILLATOR ov_patch1
	stmia	sl!, {r0-r1}
	stmia	fp!, {r6-r7}
	adds	r4, r4, #2<<16
	bcc	ov_loop
	sub	sl, sl, r4, lsl #1
skip_overlap:

	add	r8, sl, r4, lsl #1         ; reverse last half
	add	r9, sl, r4, lsl #2
rev_loop:
	ldmia	r8, {r0-r3}
	ldmdb	r9, {r6-r7,ip,lr}
	str	r0, [r9, #-4]!
	str	r1, [r9, #-4]!
	str	r2, [r9, #-4]!
	str	r3, [r9, #-4]!
	str	lr, [r8], #4
	str	ip, [r8], #4
	str	r7, [r8], #4
	str	r6, [r8], #4
	cmp	r8, r9
	bcc	rev_loop
	ldmfd	sp!, {r7,fp}

	; lapping cycle: r5 LapSrc, r6 LapDst, r7 OutBuf, r8 nAvailable*2, r9 nFromLap
	add	r5, r5, fp, lsl #1
	add	r6, r5, #0
	sub	r8, fp, r4
	cmp	r8, r4, lsl #1
	movcc	r9, r8, lsr #1
	movcs	r9, r4
	mov	ip, #0x300
	orr	ip, ip, #0xFF              ; 0x3FF
	ldr	lr, ulc_State+20           ; &LapTable (centre)
	subs	r9, r9, r9, lsl #16
	bcs	lap_from_buf
lap_out_lap:
	ldmdb	r5!, {r0-r3}
	CLIP_LOOKUP r0, ip, lr
	CLIP_LOOKUP r1, ip, lr
	CLIP_LOOKUP r2, ip, lr
	CLIP_LOOKUP r3, ip, lr
	orr	r0, r1, r0, lsl #8
	orr	r0, r2, r0, lsl #8
	orr	r0, r3, r0, lsl #8
	str	r0, [r7], #4
	adds	r9, r9, #4<<16
	bcc	lap_out_lap
lap_from_buf:
	sub	r4, r4, r4, lsl #16
	adds	r4, r4, r9, lsl #16
	bcs	lap_shift
lap_out_buf:
	ldmia	sl!, {r0-r3}
	CLIP_LOOKUP r0, ip, lr
	CLIP_LOOKUP r1, ip, lr
	CLIP_LOOKUP r2, ip, lr
	CLIP_LOOKUP r3, ip, lr
	orr	r3, r2, r3, lsl #8
	orr	r3, r1, r3, lsl #8
	orr	r3, r0, r3, lsl #8
	str	r3, [r7], #4
	adds	r4, r4, #4<<16
	bcc	lap_out_buf
lap_shift:
	subs	r8, r8, r4, lsl #1
	bls	lap_refill
lap_shift_loop:
	ldmdb	r5!, {r0-r3}
	stmdb	r6!, {r0-r3}
	subs	r8, r8, #4<<1
	bhi	lap_shift_loop
lap_refill:
	cmp	r9, #0
	beq	lap_done
lap_refill_loop:
	ldmia	sl!, {r0-r3}
	str	r0, [r6, #-4]!
	str	r1, [r6, #-4]!
	str	r2, [r6, #-4]!
	str	r3, [r6, #-4]!
	subs	r9, r9, #4
	bhi	lap_refill_loop
lap_done:

	ldmfd	sp!, {r6-r9}
	movs	r8, r8, lsr #4             ; next subblock
	bne	subblock_loop

	ldr	r0, [sp], #4               ; next channel
	sub	r0, r0, fp
	adds	r0, r0, r0, lsl #31
	ldrmi	r4, ulc_State+4            ;  restore LastSubBlockSize
	strmi	r0, [sp, #-4]!
	bmi	chan_loop
	str	r4, ulc_State+4

	tst	r0, #1                     ; M/S -> L/R, through MSTable, interleaved
	beq	ms_done
	bic	r4, r0, #1                 ; &M (S at +CH2_OFFS)
	ldr	r5, ulc_State+24           ; &MSDest
	ldr	lr, ulc_State+28           ; &MSTable
	mov	r7, #0x80000000
	mov	r8, fp
	mov	r9, #CH2_OFFS
ms_word:
	ldr	r1, [r4, r9]
	ldr	r0, [r4], #4
	MS_PAIR	sl, 0
	MS_PAIR	sl, 16
	MS_PAIR	fp, 0
	MS_PAIR	fp, 16
	stmia	r5!, {sl, fp}
	subs	r8, r8, #4
	bne	ms_word
ms_done:

	ldr	r0, ulc_State              ; save reader position
	movs	ip, r6, lsr #30
	bic	r6, r6, #0xE0000000
	adc	r6, r6, ip
	str	r6, ulc_State
	rsb	r0, r0, r6
	ldmfd	sp!, {r4-r11,pc}

ov_patchops:
	.long	0xE048804C                 ; SUB r8, r8, ip, asr #0+xx
	.long	0xE089904E                 ; ADD r9, r9, lr, asr #0+xx
ov_debruijn:
	.long	0x077CB531

; 2^15*E^(I*Pi/2*(0 + 1/2)/N), Re | Im<<16, indexed by Log2[N] (from N = 16)
ov_twiddles:
	.short	0x7FD9, 0x0648             ; 16
	.short	0x7FF6, 0x0324             ; 32
	.short	0x7FFE, 0x0192             ; 64
	.short	0x7FFF, 0x00C9             ; 128
	.short	0x8000, 0x0065             ; 256
	.short	0x8000, 0x0032             ; 512
	.short	0x8000, 0x0019             ; 1024
	.short	0x8000, 0x000D             ; 2048

ulc_State:
	.long	0, 0, 0, 0, 0, 0, 0, 0
