; ============================================================================
; ulc decoder spike: end-to-end decode benchmark (RISC OS Absolute, &8000).
;
; Decodes cfg_nblk blocks of build/stream.ulc with ulc_DecodeBlock into an
; output ring of cfg_ring blocks (stereo: L,R interleaved). cfg_table picks the
; decoder's output table: 0 = 8-bit linear (the GBA's truncation), 1 = low 8
; bits of the 11-bit sample (run 0 and 1 to recover all 11 bits; mono only),
; 2 = VIDC log, as the player uses it. The VIDC conversion is fused into the
; decoder now, so the &50000 marks only bracket an empty pass.
; The host (run_decode.py) breakpoints `mark` and reads cycles + r12:
;   &30000          before block 0
;   &30000+b+1      after block b
;   &50000/&50001   around the VIDC pass
;   &EEEE           not enough memory (buffers past the RAM limit)
; The words at &8010 give ulcfile, outbuf, vidcbuf, ulc_State and buf_end: vasm's
; listing symbol table can be stale with -opt-adr, so the host reads these.
; ============================================================================

.equ OS_WriteC, 0x00
.equ OS_GetEnv, 0x10
.equ OS_Exit,   0x11
.equ OS_WriteI, 0x100

.equ CH2_OFFS, 0x10000                     ; 64 KB per channel of 8-bit output

.org 0x8000

start:
	b	main
cfg_mode:
	.long	255                        ; &8004: screen mode (255 = leave), host-patched
cfg_nblk:
	.long	1                          ; &8008: blocks to decode, host-patched
mark:                                      ; &800C: host breakpoint
	mov	pc, lr
addr_table:                                ; &8010: final addresses for the host
	.long	ulcfile, outbuf, vidcbuf, ulc_State, buf_end
cfg_ring:
	.long	0x7FFFFFFF                 ; &8024: output ring size in blocks, host-patched
cfg_table:
	.long	0                          ; &8028: output table (see above), host-patched

main:
	swi	OS_GetEnv
	mov	sp, r1
	ldr	r0, p_end
	cmp	r0, r1
	movhi	r12, #0xEE00
	orrhi	r12, r12, #0xEE
	blhi	mark
	swihi	OS_Exit
	ldr	r0, cfg_mode
	cmp	r0, #255
	beq	main_nomode
	swi	OS_WriteI+22
	swi	OS_WriteC
main_nomode:

	ldr	r0, p_lap                  ; clear both lapping buffers
	mov	r1, #4*2048
	mov	r2, #0
	mov	r3, #0
	mov	r4, #0
	mov	r5, #0
clr_lap:
	stmia	r0!, {r2-r5}
	subs	r1, r1, #16
	bgt	clr_lap

	adr	r0, ulcfile                ; parse header
	ldr	r1, [r0, #4]
	mov	r1, r1, lsl #16
	mov	r1, r1, lsr #16            ; BlockSize
	ldr	r2, [r0, #16]
	mov	r2, r2, lsl #16
	mov	r2, r2, lsr #16            ; nChan
	ldr	r3, [r0, #20]
	add	r3, r0, r3                 ; &stream
	adr	r4, ulc_State              ; {&NextData, LastSubBlockSize, BlockSize, nChan, &OutBuf,
	mov	r5, #0                     ;  &LapTable, &MSDest, &MSTable}
	ldr	r6, p_out
	str	r3, [r4, #0]
	str	r5, [r4, #4]
	str	r1, [r4, #8]
	str	r2, [r4, #12]
	cmp	r2, #2
	strne	r6, [r4, #16]              ; mono: the decoder writes the ring
	streq	r6, [r4, #24]              ; stereo: M/S writes the ring,
	ldreq	r6, p_vidc                 ;  lapping goes via a scratch buffer
	streq	r6, [r4, #16]
	ldr	r0, cfg_table
	adr	r6, tab_lin8 + 1024
	adr	r7, tab_ident
	cmp	r0, #1
	adreq	r6, tab_low8 + 1024
	cmp	r0, #2
	adreq	r6, tab_vidc11 + 1024
	adreq	r7, vidc_table
	cmp	r2, #2                     ; stereo M/S needs 8-bit linear from lapping
	adreq	r6, tab_lin8 + 1024
	str	r6, [r4, #20]
	str	r7, [r4, #28]

	ldr	r10, cfg_nblk
	mov	r9, #0                     ; block index
	mov	r8, #0                     ; block index within the output ring
	mov	r12, #0x30000
	bl	mark
blk_loop:
	bl	ulc_DecodeBlock
	add	r9, r9, #1
	add	r8, r8, #1                 ; next slot in the output ring
	ldr	r1, cfg_ring
	cmp	r8, r1
	movhs	r8, #0
	ldr	r0, ulc_State+8
	ldr	r1, ulc_State+12
	mul	r0, r1, r0                 ; bytes per block
	mul	r0, r8, r0
	ldr	r1, p_out
	add	r0, r1, r0
	ldr	r1, ulc_State+12
	cmp	r1, #2
	strne	r0, ulc_State+16
	streq	r0, ulc_State+24
	mov	r12, #0x30000
	add	r12, r12, r9
	bl	mark
	cmp	r9, r10
	bcc	blk_loop

	mov	r12, #0x50000
	bl	mark
vidc_done:
	mov	r12, #0x50000
	orr	r12, r12, #1
	bl	mark
	swi	OS_Exit

p_lap:	.long	ulc_TransformBuffer + 4*2048
p_out:	.long	outbuf
p_vidc:	.long	vidcbuf
p_end:	.long	buf_end

.include "decoder.asm"
.include "fourier.asm"

.align 4
vidc_table:
	.incbin	"build/vidc_table.bin"     ; 256 bytes, written by run_decode.py
tab_ident:
	.incbin	"build/ident256.bin"
tab_lin8:
	.incbin	"build/lin11_8.bin"        ; 2 KB each, indexed from the centre
tab_low8:
	.incbin	"build/lin11_lo.bin"
tab_vidc11:
	.incbin	"build/vidc11.bin"

.align 4
ulcfile:
	.incbin	"build/stream.ulc"

.align 4
image_end:
; Work buffers live past the image so they don't bloat the file.
.equ ulc_TransformBuffer, (image_end + 15) & ~15   ; 8 KB transform, then 8 KB lapping (L, R)
.equ outbuf,  ulc_TransformBuffer + 4*4096
.equ vidcbuf, outbuf + 2*CH2_OFFS
.equ ulc_FFTBuffer, vidcbuf + 2*CH2_OFFS                 ; 8 KB: the DCT-IV's output
.equ buf_end, ulc_FFTBuffer + 4*2048
