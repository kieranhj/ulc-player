; ============================================================================
; ulc IMDCT spike: benchmark harness (RISC OS Absolute, loads at &8000).
;
; The host (run_bench.py) breakpoints `mark` and reads the cycle counter and
; r12 (a tag saying what just happened) on every hit:
;   tag 0          start
;   tag N          about to transform N points
;   tag N|&10000   finished; output is in `buf`
;   tag &2000x     MUL micro-benchmark checkpoints
; ============================================================================

.equ OS_WriteC, 0x00
.equ OS_GetEnv, 0x10
.equ OS_Exit,   0x11
.equ OS_WriteI, 0x100

.org 0x8000

start:
	b	main
cfg_mode:
	.long	255                        ; screen mode to select (255 = leave alone); patched by host
mark:                                      ; &8008: host breakpoint
	mov	pc, lr

main:
	swi	OS_GetEnv                  ; r1 = RAM limit: Absolute files get no stack
	mov	sp, r1
	ldr	r0, cfg_mode
	cmp	r0, #255
	beq	main_nomode
	swi	OS_WriteI+22
	swi	OS_WriteC
main_nomode:
	mov	r12, #0
	bl	mark

	adr	r11, n_table
n_loop:
	ldr	r10, [r11], #4
	cmp	r10, #0
	beq	mul_bench
	adr	r0, input                  ; copy fresh input (transform is in place)
	ldr	r1, p_buf
	mov	r2, r10
n_copy:
	ldmia	r0!, {r3-r6}
	stmia	r1!, {r3-r6}
	subs	r2, r2, #4
	bgt	n_copy
	mov	r12, r10
	bl	mark
	ldr	r0, p_buf
	mov	r1, r10
	bl	Fourier_DCT4                ; r0 = result (the host reads it there)
	orr	r12, r10, #0x10000
	bl	mark
	b	n_loop

; MUL timing: 64 MULs per run with Rs = +0x7FF6, -0x7FF6, +0x7F, then 64 NOPs.
mul_bench:
	mov	r2, #0x3000
	orr	r2, r2, #0x39
	ldr	r1, rs_pos
	mov	r12, #0x20000
	bl	mark
	.rept 64
	mul	r0, r2, r1
	.endr
	ldr	r1, rs_neg
	mov	r12, #0x20000
	orr	r12, r12, #1
	bl	mark
	.rept 64
	mul	r0, r2, r1
	.endr
	ldr	r1, rs_small
	mov	r12, #0x20000
	orr	r12, r12, #2
	bl	mark
	.rept 64
	mul	r0, r2, r1
	.endr
	ldr	r1, rs_small
	mov	r12, #0x20000
	orr	r12, r12, #3
	bl	mark
	.rept 64
	mov	r0, r0
	.endr
	ldr	r1, rs_small
	mov	r12, #0x20000
	orr	r12, r12, #4
	bl	mark
	swi	OS_Exit

rs_pos:   .long 0x7FF6
rs_neg:   .long -0x7FF6
rs_small: .long 0x7F
p_buf:    .long buf

; Each size twice: the first run warms the ARM3 cache, the second is reported.
n_table:
	.long	64, 64, 128, 128, 256, 256, 512, 512, 1024, 1024, 2048, 2048, 0

.include "fourier.asm"

.align 4
input:
	.incbin	"build/input.bin"          ; 2048 words, written by run_bench.py

.align 4
buf_start:
.equ buf, (buf_start + 15) & ~15
.equ ulc_FFTBuffer, buf + 4*2048
