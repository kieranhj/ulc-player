; ============================================================================
; ULC player v0 for RISC OS 3.1x on ARM2 / ARM250 / ARM3.
; Also plays IMA ADPCM ("ADP4", see adpcm_encode.py) through the identical
; output path, so the two codecs can be compared like for like.
;
;   *WimpSlot -min 1600K
;   *player <file>          ; a .ulc file (ULC2, mono or M/S stereo, N <= 2048)
;   *player <app dir>       ; menu of the tracks in <app dir>.Tracks (the !App)
;
; Sound: our own SoundDMA channel handler (Sound_Configure). Its fill routine,
; called from the sound interrupt, only copies VIDC log bytes out of a 32 KB
; ring buffer (or plays silence and counts an underrun if the ring is empty).
; Decoding happens in the foreground: whenever the ring has room for a block,
; decode it (spike/decoder.asm), convert to VIDC log bytes, and append it.
; The track loops. Escape quits and restores the sound system.
;
; Sound handling follows QTM (see Repos/qtm-vasm, qtm_dma.asm):
;  - always pass the sample period in r2 to Sound_Configure (RISC OS 3 bug);
;  - read back the DMA size actually granted (the fill copes with any
;    16-byte multiple, wrapping round the ring);
;  - "twang" Sound_Configure again after Sound_Stereo so positions take;
;  - on exit, output silence for a few buffers, restore the old config, then
;    clear the old channels' control blocks so OS voices don't buzz.
;
; Screen: MODE 9 (4 bpp: half MODE 13's video DMA, which costs ARM2 ~9%) with
; track info, CPU % (decode time / wall time, measured
; with OS_ReadMonotonicTime over 2 s windows), ring fill, underruns, and a scope.
; ============================================================================

.equ OS_WriteC,            0x00
.equ OS_Write0,            0x02
.equ OS_NewLine,           0x03
.equ OS_Byte,              0x06
.equ OS_Word,              0x07
.equ OS_GetEnv,            0x10
.equ OS_Exit,              0x11
.equ OS_ReadEscapeState,   0x2C
.equ OS_ReadVduVariables,  0x31
.equ OS_RemoveCursors,     0x36
.equ OS_ReadMonotonicTime, 0x42
.equ OS_ConvertCardinal4,  0xD8
.equ OS_Mouse,             0x1C
.equ OS_WriteI,            0x100
.equ XOS_File,             0x20008
.equ Sound_Configure,      0x40140
.equ Sound_Enable,         0x40141
.equ Sound_Stereo,         0x40142
.equ Sound_WriteControlBlock, 0x4018C

.equ CH2_OFFS,     0x800                   ; decoder output: channel 2 at +2 KB
.equ RING_SIZE,    32768                   ; must be a power of two
.equ RING_SHIFT,   17                      ; 32 - log2(RING_SIZE)
.equ DMA_SAMPLES,  256                     ; samples per channel per DMA buffer
.equ SCOPE_MID,    176                     ; scope centre line (pixel row)
.equ SCREEN_MODE,  9                       ; 320x256, 16 colours, 40x32 text
.equ SCOPE_COL,    7                       ; white (8-15 flash in MODE 9)
.equ STATUS_CS,    100                     ; status text redraw period
.equ SCOPE_CS,     20                      ; scope redraw period

.org 0x8000

start:
	b	main
info_table:                                ; &8004: for the host test driver
	.long	underruns, cpu_pct, blocks_played, ring_rd, ring_wr, dma_samples, ring

; ----------------------------------------------------------------------------
main:
	swi	OS_GetEnv                  ; r0 = command line, r1 = RAM limit
	mov	sp, r1
	str	r1, ram_limit

	; -- argument: second word of the command line
skip_name:
	ldrb	r1, [r0], #1
	cmp	r1, #32
	bhi	skip_name
	sub	r0, r0, #1
skip_space:
	ldrb	r1, [r0]
	cmp	r1, #32
	addeq	r0, r0, #1
	beq	skip_space
	blo	err_usage
	adr	r2, argbuf
	mov	r3, #200
copy_name:
	ldrb	r1, [r0], #1
	cmp	r1, #32
	movls	r1, #0
	strb	r1, [r2], #1
	subhis	r3, r3, #1
	bhi	copy_name
	mov	r1, #0
	strb	r1, [r2]

	mov	r0, #229                   ; Escape key gives ASCII 27, not an
	mov	r1, #1                     ; escape condition (restored on exit)
	mov	r2, #0
	swi	OS_Byte
	str	r1, old_esc

	mov	r0, #17                    ; a directory means menu mode
	adr	r1, argbuf
	swi	XOS_File
	bvs	err_os
	cmp	r0, #2
	beq	menu_init
	adr	r0, argbuf
	str	r0, path_ptr
	str	r0, title_ptr
	bl	play_file
	b	quit

; ----------------------------------------------------------------------------
; Menu: pick a track from <dir>.Tracks with keys 1-4 or the mouse.

; Each item is a band of text rows so it's an easy target for a finger.
.equ MENU_ROW0,  7                         ; first entry's text row
.equ MENU_N,     4                         ; entries in track_table
.equ MENU_SHIFT, 2                         ; 1 << MENU_SHIFT text rows per entry
.equ MENU_QUIT,  25                        ; Quit: rows 23..27
.equ MSG_ROW,    22
.equ TAP_QUIET,  15                        ; cs of stillness either side of a tap
.equ TAP_MIN,    16                        ; OS units a tap must jump

menu_init:
	mov	r0, #1
	str	r0, menu_mode
menu:
	ldr	sp, ram_limit
	bl	draw_menu
menu_wait:
	bl	read_input
	cmp	r0, #0
	beq	menu_wait
	cmp	r0, #27
	beq	quit
	orr	r1, r0, #0x20
	cmp	r1, #113                   ; 'q'
	beq	quit
	sub	r1, r0, #49                ; '1'..'8'
	cmp	r1, #MENU_N
	blo	menu_pick
	cmp	r0, #0x100                 ; mouse click: 0x100 + text row
	blo	menu_wait
	sub	r1, r0, #0x100
	cmp	r1, #MENU_QUIT-2
	blo	mw_track
	cmp	r1, #MENU_QUIT+3
	blo	quit
	b	menu_wait
mw_track:
	subs	r1, r1, #MENU_ROW0-1
	bmi	menu_wait
	cmp	r1, #MENU_N<<MENU_SHIFT
	bhs	menu_wait
	mov	r1, r1, lsr #MENU_SHIFT
menu_pick:
	adr	r2, track_table            ; {leaf, display name, menu line}
	add	r2, r2, r1, lsl #2
	add	r2, r2, r1, lsl #3
	ldmia	r2, {r3, r4}
	str	r4, title_ptr
	adr	r0, fname                  ; <dir>.Tracks.<leaf>
	adr	r1, argbuf
	bl	strcpy
	adr	r1, s_tracks
	bl	strcpy
	mov	r1, r3
	bl	strcpy
	adr	r0, fname
	str	r0, path_ptr
	mov	r0, #1
	mov	r1, #MSG_ROW
	adr	r2, s_loading
	bl	print_at
	bl	play_file
	b	menu

draw_menu:
	stmfd	sp!, {r0-r5, lr}
	swi	OS_WriteI+22
	swi	OS_WriteI+SCREEN_MODE
	swi	OS_RemoveCursors
	mov	r0, #106                   ; mouse pointer on
	mov	r1, #1
	swi	OS_Byte
	mov	r0, #21                    ; flush the keyboard buffer
	mov	r1, #0
	swi	OS_Byte
	mov	r0, #1
	mov	r1, #2
	adr	r2, s_menu_title
	bl	print_at
	mov	r0, #1
	mov	r1, #4
	adr	r2, s_menu_fmt
	bl	print_at
	mov	r0, #1
	mov	r1, #5
	adr	r2, s_menu_fmt2
	bl	print_at
	adr	r4, track_table
	mov	r5, #MENU_ROW0
dm_loop:
	mov	r0, #2
	mov	r1, r5
	ldr	r2, [r4, #8]
	bl	print_at
	add	r4, r4, #12
	add	r5, r5, #1<<MENU_SHIFT
	cmp	r5, #MENU_ROW0+(MENU_N<<MENU_SHIFT)
	blo	dm_loop
	mov	r0, #2
	mov	r1, #MENU_QUIT
	adr	r2, s_menu_quit
	bl	print_at
	mov	r0, #1
	mov	r1, #28
	adr	r2, s_menu_help
	bl	print_at
	mov	r0, #1
	mov	r1, #29
	adr	r2, s_menu_help2
	bl	print_at
	bl	set_mouse_box
	bl	tap_sync
	ldmfd	sp!, {r0-r5, pc}

; r0 = 0 (nothing), a key code, or 0x100 + text row of a new mouse click.
read_input:
	stmfd	sp!, {r1-r3, lr}
	mov	r0, #129                   ; INKEY(0)
	mov	r1, #0
	mov	r2, #0
	swi	OS_Byte
	cmp	r2, #0
	moveq	r0, r1
	beq	ri_done
	cmp	r2, #27                    ; escape condition after all
	bne	ri_mouse
	mov	r0, #126
	swi	OS_Byte
	mov	r0, #27
	b	ri_done
ri_mouse:
	swi	OS_Mouse                   ; r0 = x, r1 = y (OS units), r2 = buttons
	ldr	r3, mouse_prev
	str	r2, mouse_prev
	cmp	r2, #0                     ; a new button press: a click at y
	beq	ri_tap
	cmp	r3, #0
	beq	ri_row
ri_tap:
	; Archimedes Live on a phone moves the pointer to a tap but never shows
	; the button: the browser sends mousedown and mouseup together, between
	; two of the emulator's 10 ms keyboard polls. So a tap is a single jump of
	; the pointer with stillness before and after; a real mouse moves in a
	; stream of small steps, one per 20 ms frame.
	stmfd	sp!, {r4-r7}
	ldr	r4, tap_x
	ldr	r5, tap_y
	str	r0, tap_x
	str	r1, tap_y
	subs	r6, r0, r4
	rsbmi	r6, r6, #0
	subs	r7, r1, r5
	rsbmi	r7, r7, #0
	add	r6, r6, r7                 ; distance moved since the last call
	swi	OS_ReadMonotonicTime
	ldr	r4, tap_last
	cmp	r6, #0
	beq	ri_still
	str	r0, tap_last               ; moved: a tap candidate if it was still
	sub	r4, r0, r4                 ; before and the jump is big enough
	mov	r5, #0
	cmp	r4, #TAP_QUIET
	cmphs	r6, #TAP_MIN
	movhs	r5, #1
	str	r5, tap_pending
	mov	r0, #0
	ldmfd	sp!, {r4-r7}
	b	ri_done
ri_still:
	ldr	r5, tap_pending            ; and it stays still after: a tap
	sub	r4, r0, r4
	mov	r0, #0
	cmp	r5, #0
	beq	ri_none
	cmp	r4, #TAP_QUIET
	blo	ri_none
	str	r0, tap_pending
	ldr	r1, tap_y
	ldmfd	sp!, {r4-r7}
	b	ri_row
ri_none:
	ldmfd	sp!, {r4-r7}
	b	ri_done
ri_row:
	cmp	r1, #1024
	movge	r1, #1020
	cmp	r1, #0
	movlt	r1, #0
	rsb	r1, r1, #1024
	sub	r1, r1, #1
	mov	r1, r1, lsr #5             ; 32 OS units per text row (MODE 9 / 13)
	add	r0, r1, #0x100
ri_done:
	ldmfd	sp!, {r1-r3, pc}

tap_sync:                                  ; forget pointer moves so far
	stmfd	sp!, {r0-r3, lr}
	swi	OS_Mouse
	str	r0, tap_x
	str	r1, tap_y
	str	r2, mouse_prev
	swi	OS_ReadMonotonicTime
	str	r0, tap_last
	mov	r0, #0
	str	r0, tap_pending
	ldmfd	sp!, {r0-r3, pc}

set_mouse_box:                             ; OS_Word 21,1: the whole MODE 9
	stmfd	sp!, {r0-r1, lr}           ; screen (Archimedes Live clamps
	mov	r0, #21                    ; absolute positions to the last box
	adr	r1, mouse_box              ; set by SWI, i.e. the desktop's)
	swi	OS_Word
	ldmfd	sp!, {r0-r1, pc}

strcpy:                                    ; r0 dest (returned at the 0), r1 src
	ldrb	r2, [r1], #1
	strb	r2, [r0], #1
	cmp	r2, #0
	bne	strcpy
	sub	r0, r0, #1
	mov	pc, lr

; ----------------------------------------------------------------------------
; Play the file at path_ptr until a key or click. Sound is stopped on return.

play_file:
	stmfd	sp!, {lr}
	mov	r0, #0
	str	r0, ring_rd
	str	r0, ring_wr
	str	r0, underruns
	str	r0, blocks_played
	str	r0, stopping
	str	r0, fmt_adpcm
	str	r0, cpu_pct
	str	r0, dec_cs

	; -- load the file
	mov	r0, #17                    ; read catalogue info
	ldr	r1, path_ptr
	swi	XOS_File
	bvs	err_os
	cmp	r0, #1
	bne	err_notfound
	ldr	r2, p_file
	add	r3, r2, r4                 ; end of file in memory
	add	r3, r3, #16384             ; + stack
	ldr	r5, ram_limit
	cmp	r3, r5
	bhi	err_memory
	mov	r0, #255                   ; load at our address
	ldr	r1, path_ptr
	ldr	r2, p_file
	mov	r3, #0
	swi	XOS_File
	bvs	err_os

	; -- parse the header
	ldr	r0, p_file
	ldr	r1, [r0]
	ldr	r2, adp_magic
	cmp	r1, r2
	beq	parse_adpcm
	ldr	r2, ulc_magic
	cmp	r1, r2
	bne	err_format
	ldr	r1, [r0, #4]
	mov	r1, r1, lsl #16
	mov	r1, r1, lsr #16            ; BlockSize
	cmp	r1, #64
	blo	err_format
	cmp	r1, #2048
	bhi	err_format
	str	r1, hdr_bs
	ldr	r2, [r0, #8]
	str	r2, hdr_nblk
	ldr	r2, [r0, #12]
	str	r2, hdr_rate
	ldr	r2, [r0, #16]
	mov	r3, r2, lsr #16            ; RateKbps
	str	r3, hdr_kbps
	mov	r2, r2, lsl #16
	mov	r2, r2, lsr #16            ; nChan
	cmp	r2, #1
	cmpne	r2, #2
	bne	err_format
	str	r2, hdr_nch
	mul	r3, r1, r2
	str	r3, block_bytes
	ldr	r2, [r0, #20]
	add	r2, r0, r2
	str	r2, stream_start
	b	header_done

parse_adpcm:                               ; 'ADP4' header (see adpcm_encode.py)
	mov	r1, #1
	str	r1, fmt_adpcm
	ldr	r1, [r0, #16]              ; samples per channel per block
	cmp	r1, #64
	blo	err_format
	cmp	r1, #2048
	bhi	err_format
	str	r1, hdr_bs
	ldr	r2, [r0, #4]
	str	r2, hdr_rate
	ldr	r2, [r0, #12]
	str	r2, hdr_nblk
	ldr	r2, [r0, #24]
	str	r2, hdr_kbps
	ldr	r2, [r0, #28]              ; bits per code (0: 4, older files),
	bic	r2, r2, #0x100             ;  + 0x100 if blocks carry a predictor mode
	cmp	r2, #0
	moveq	r2, #4
	cmp	r2, #2
	blo	err_format
	cmp	r2, #4
	bhi	err_format
	str	r2, adp_bits
	ldr	r2, [r0, #8]
	cmp	r2, #1
	cmpne	r2, #2
	bne	err_format
	str	r2, hdr_nch
	mul	r3, r1, r2
	str	r3, block_bytes
	ldr	r2, [r0, #20]
	add	r2, r0, r2
	str	r2, stream_start
header_done:

	; -- sample period in us = round(1e6 / rate)
	ldr	r1, hdr_rate
	ldr	r0, one_million
	add	r0, r0, r1, lsr #1
	bl	udiv
	str	r0, period_us

	bl	reset_decoder
	bl	setup_screen

	; -- fill the ring before starting sound
prefill:
	bl	ring_free
	ldr	r1, block_bytes
	cmp	r0, r1
	blo	prefill_done
	bl	decode_one
	b	prefill
prefill_done:

	bl	sound_start
	swi	OS_ReadMonotonicTime
	str	r0, win_start
	str	r0, last_draw
	str	r0, last_status
	mov	r1, #0
	str	r1, dec_cs

; ----------------------------------------------------------------------------
main_loop:
	bl	read_input
	cmp	r0, #0
	bne	play_done
	bl	ring_free
	ldr	r1, block_bytes
	cmp	r0, r1
	blo	idle
	bl	decode_one
	b	main_loop
idle:
	swi	OS_ReadMonotonicTime
	ldr	r1, last_draw
	sub	r2, r0, r1
	cmp	r2, #SCOPE_CS              ; scope every 0.2 s
	blo	main_loop
	str	r0, last_draw
	bl	draw_scope
	ldr	r1, last_status            ; status text every 1 s: the VDU calls
	sub	r2, r0, r1                 ; behind it cost ~3% of an A3000 at 5 Hz
	cmp	r2, #STATUS_CS
	blo	main_loop
	str	r0, last_status
	ldr	r1, win_start              ; CPU % over 2 s windows
	sub	r2, r0, r1
	cmp	r2, #200
	blo	no_cpu
	str	r0, win_start
	mov	r1, r2
	ldr	r0, dec_cs
	mov	r3, #100
	mul	r0, r3, r0
	bl	udiv
	str	r0, cpu_pct
	mov	r0, #0
	str	r0, dec_cs
no_cpu:
	bl	draw_status
	b	main_loop

play_done:
	bl	sound_stop
	ldmfd	sp!, {pc}

quit:
	bl	restore_escape
	mov	r0, #106                   ; pointer off
	mov	r1, #0
	swi	OS_Byte
	swi	OS_WriteI+22
	swi	OS_WriteI+12
	swi	OS_Exit

; ----------------------------------------------------------------------------
; Decoder glue

reset_decoder:
	stmfd	sp!, {r0-r5, lr}
	adr	r0, ulc_State              ; {&NextData, LastSubBlockSize, BlockSize, nChan, &OutBuf,
	ldr	r1, stream_start           ;  &LapTable, &MSDest, &MSTable}
	str	r1, [r0, #0]
	mov	r1, #0
	str	r1, [r0, #4]
	ldr	r1, hdr_bs
	str	r1, [r0, #8]
	ldr	r1, hdr_nch
	str	r1, [r0, #12]
	cmp	r1, #2
	ldr	r1, p_outbuf               ; stereo: lapping -> outbuf (8-bit linear),
	str	r1, [r0, #16]              ;  then M/S -> ring (VIDC, interleaved)
	ldreq	r1, p_lin11
	ldrne	r1, p_vidc11               ; mono: lapping -> ring (VIDC), &OutBuf set per block
	str	r1, [r0, #20]
	adr	r1, vidc_table
	str	r1, [r0, #28]
	ldr	r1, hdr_nblk
	str	r1, blocks_left
	ldr	r1, stream_start
	str	r1, adp_next
	ldr	r0, p_lap                  ; clear both lapping buffers (8 KB)
	mov	r1, #4*2048
	mov	r2, #0
	mov	r3, #0
	mov	r4, #0
	mov	r5, #0
rd_clear:
	stmia	r0!, {r2-r5}
	subs	r1, r1, #16
	bgt	rd_clear
	ldmfd	sp!, {r0-r5, pc}

decode_one:                                ; decode one block into the ring
	stmfd	sp!, {r0-r11, lr}
	swi	OS_ReadMonotonicTime
	mov	r11, r0
	ldr	r1, p_ring                 ; both decoders write VIDC bytes straight
	ldr	r2, ring_wr                ; into the ring (a block never straddles
	mov	r2, r2, lsl #RING_SHIFT    ; its end: block sizes are powers of two)
	add	r0, r1, r2, lsr #RING_SHIFT
	str	r0, last_block
	ldr	r1, fmt_adpcm              ; NB: a 26-bit "LDMFD sp!, {...,pc}" return
	cmp	r1, #0                     ; doesn't restore the flags, so never
	bne	d1_adpcm                   ; condition anything on flags after a BL
	ldr	r1, hdr_nch
	cmp	r1, #2
	ldr	r2, p_ulc_state
	streq	r0, [r2, #24]              ; stereo: the M/S step writes the ring
	strne	r0, [r2, #16]              ; mono: lapping writes the ring
	bl	ulc_DecodeBlock
	b	d1_done
d1_adpcm:
	bl	adpcm_decode_block
d1_done:
	ldr	r2, ring_wr
	ldr	r3, block_bytes
	add	r2, r2, r3
	str	r2, ring_wr                ; publish (single store)
	swi	OS_ReadMonotonicTime
	sub	r0, r0, r11
	ldr	r1, dec_cs
	add	r1, r1, r0
	str	r1, dec_cs
	ldr	r0, blocks_played
	add	r0, r0, #1
	str	r0, blocks_played
	ldr	r0, blocks_left
	subs	r0, r0, #1
	str	r0, blocks_left
	bleq	reset_decoder              ; loop the track
	ldmfd	sp!, {r0-r11, pc}

ring_free:                                 ; r0 = free bytes in the ring
	ldr	r0, ring_wr
	ldr	r1, ring_rd
	sub	r0, r0, r1
	rsb	r0, r0, #RING_SIZE
	mov	pc, lr

; ----------------------------------------------------------------------------
; Sound

sound_start:
	stmfd	sp!, {r0-r5, lr}
	mov	r0, #0                     ; read the current configuration
	mov	r1, #0
	mov	r2, #0
	mov	r3, #0
	mov	r4, #0
	swi	Sound_Configure
	adr	r5, old_config
	stmia	r5, {r0-r4}
	add	r3, r3, #8                 ; keep the OS log/scale table pointers
	ldmia	r3, {r0-r1}
	adr	r5, handler_tables
	stmia	r5, {r0-r1}
	ldr	r0, hdr_nch
	mov	r1, #DMA_SAMPLES
	ldr	r2, period_us
	adr	r3, channel_handler
	mov	r4, #0
	swi	Sound_Configure
	ldr	r0, hdr_nch
	cmp	r0, #2
	beq	ss_stereo
	mov	r0, #1                     ; mono: channel 1 centred (VIDC's 8 stereo
	mov	r1, #0                     ; image registers may hold another app's
	swi	Sound_Stereo               ; positions)
	str	r1, old_stereo1
	b	ss_twang
ss_stereo:
	mov	r0, #1                     ; channel 1 hard left, channel 2 hard right
	mvn	r1, #126                   ; -127
	swi	Sound_Stereo
	str	r1, old_stereo1
	mov	r0, #2
	mov	r1, #127
	swi	Sound_Stereo
	str	r1, old_stereo2
ss_twang:
	ldr	r0, hdr_nch                ; twang so the stereo positions take effect
	mov	r1, #DMA_SAMPLES
	ldr	r2, period_us
	adr	r3, channel_handler
	mov	r4, #0
	swi	Sound_Configure
ss_enable:
	mov	r0, #0                     ; read back the DMA size actually granted
	mov	r1, #0
	mov	r2, #0
	mov	r3, #0
	mov	r4, #0
	swi	Sound_Configure
	str	r1, dma_samples
	mov	r0, #2
	swi	Sound_Enable
	str	r0, old_enable
	ldmfd	sp!, {r0-r5, pc}

sound_stop:
	stmfd	sp!, {r0-r5, lr}
	mov	r0, #1                     ; fill silence from now on
	str	r0, stopping
	swi	OS_ReadMonotonicTime       ; let a few DMA buffers go by
	add	r5, r0, #4
sst_wait:
	swi	OS_ReadMonotonicTime
	cmp	r0, r5
	blo	sst_wait
	adr	r5, old_config
	ldmia	r5, {r0-r3}
	mov	r4, #0
	swi	Sound_Configure
	ldr	r4, old_config             ; clear the old channels' control blocks
sst_sccb:
	mov	r0, r4
	mov	r1, #0x0C
	mov	r2, #0
	swi	Sound_WriteControlBlock
	subs	r4, r4, #1
	bgt	sst_sccb
	mov	r0, #1
	ldr	r1, old_stereo1
	swi	Sound_Stereo
	ldr	r0, hdr_nch
	cmp	r0, #2
	bne	sst_enable
	mov	r0, #2
	ldr	r1, old_stereo2
	swi	Sound_Stereo
sst_enable:
	ldr	r0, old_enable
	swi	Sound_Enable
	ldmfd	sp!, {r0-r5, pc}

channel_handler:
	.long	fill_code
	.long	overrun_code
handler_tables:
	.long	0, 0                       ; OS linear-to-log and log-scale tables

; Called from the sound interrupt: r12 = DMA buffer start, r10 = end.
; The buffer is interleaved by channel, which is how the ring is stored.
fill_code:
	stmfd	sp!, {r0-r12, lr}
	sub	r8, r10, r12               ; bytes wanted (a multiple of 16)
	ldr	r0, stopping
	cmp	r0, #0
	bne	fill_zeros
	ldr	r0, ring_rd
	ldr	r1, ring_wr
	sub	r2, r1, r0
	cmp	r2, r8
	blo	fill_silence
	ldr	r6, p_ring
	mov	r2, r0, lsl #RING_SHIFT
	add	r1, r6, r2, lsr #RING_SHIFT
	add	r7, r6, #RING_SIZE         ; ring end, for wrapping
	add	r0, r0, r8
fill_copy:
	ldmia	r1!, {r2-r5}
	stmia	r12!, {r2-r5}
	cmp	r1, r7
	moveq	r1, r6
	subs	r8, r8, #16
	bgt	fill_copy
	str	r0, ring_rd
	ldmfd	sp!, {r0-r12, pc}
fill_silence:
	ldr	r0, underruns
	add	r0, r0, #1
	str	r0, underruns
fill_zeros:
	mov	r2, #0
	mov	r3, #0
	mov	r4, #0
	mov	r5, #0
fill_zero:
	stmia	r12!, {r2-r5}
	subs	r8, r8, #16
	bgt	fill_zero
	ldmfd	sp!, {r0-r12, pc}

overrun_code:
	mov	pc, lr

; ----------------------------------------------------------------------------
; Screen

setup_screen:
	stmfd	sp!, {r0-r3, lr}
	swi	OS_WriteI+22
	swi	OS_WriteI+SCREEN_MODE
	swi	OS_RemoveCursors
	adr	r0, vdu_vars
	adr	r1, scr_start
	swi	OS_ReadVduVariables
	adr	r0, oldy                   ; scope: nothing drawn yet
	mov	r1, #SCOPE_MID
	mov	r2, #320
ssc_init:
	strb	r1, [r0], #1
	subs	r2, r2, #1
	bgt	ssc_init
	mov	r0, #1
	mov	r1, #1
	adr	r2, s_title
	ldr	r3, fmt_adpcm
	cmp	r3, #0
	adrne	r2, s_title_adpcm
	bl	print_at
	mov	r0, #106                   ; mouse pointer on (click to stop)
	mov	r1, #1
	swi	OS_Byte
	mov	r0, #1
	mov	r1, #3
	ldr	r2, title_ptr
	bl	print_at
	mov	r0, #1                     ; "<rate> Hz  <n> ch  N=<bs>  <kbps> kbps"
	mov	r1, #4
	adr	r2, s_empty
	bl	print_at
	ldr	r0, hdr_rate
	bl	print_num
	adr	r0, s_hz
	swi	OS_Write0
	ldr	r0, hdr_nch
	bl	print_num
	adr	r0, s_ch
	swi	OS_Write0
	ldr	r0, hdr_bs
	bl	print_num
	adr	r0, s_sp2
	swi	OS_Write0
	ldr	r0, hdr_kbps
	bl	print_num
	adr	r0, s_kbps
	swi	OS_Write0
	mov	r0, #1
	mov	r1, #31
	adr	r2, s_escape
	bl	print_at
	bl	set_mouse_box
	bl	tap_sync
	ldmfd	sp!, {r0-r3, pc}

draw_status:
	stmfd	sp!, {r0-r3, lr}
	mov	r0, #1
	mov	r1, #6
	adr	r2, s_cpu
	bl	print_at
	ldr	r0, cpu_pct
	bl	print_num
	adr	r0, s_pct
	swi	OS_Write0
	mov	r0, #1
	mov	r1, #7
	adr	r2, s_buffer
	bl	print_at
	ldr	r0, ring_wr
	ldr	r1, ring_rd
	sub	r0, r0, r1
	bl	print_num
	adr	r0, s_bytes
	swi	OS_Write0
	mov	r0, #1
	mov	r1, #8
	adr	r2, s_under
	bl	print_at
	ldr	r0, underruns
	bl	print_num
	adr	r0, s_pad
	swi	OS_Write0
	mov	r0, #1
	mov	r1, #9
	adr	r2, s_blocks
	bl	print_at
	ldr	r0, blocks_played
	bl	print_num
	adr	r0, s_pad
	swi	OS_Write0
	ldmfd	sp!, {r0-r3, pc}

; Scope of the most recent block, channel 1: 320 columns, rows 112..240.
; Reads the VIDC bytes in the ring, through a VIDC -> signed offset table.
; MODE 9: 160 bytes per row, two pixels per byte (left pixel in the low nybble).
draw_scope:
	stmfd	sp!, {r0-r11, lr}
	ldr	r0, scr_start
	ldr	r1, last_block
	adr	r2, oldy
	ldr	r3, hdr_bs
	ldr	r10, hdr_nch
	mov	r3, r3, lsl r10            ; byte offset = x*N*nch/512
	sub	r10, r10, #1               ; stereo: round to the even (left) byte
	adr	r8, scope_table
	mov	r4, #0                     ; x
	mov	r9, #160
dsc_loop:
	and	r11, r4, #1
	mov	r11, r11, lsl #2           ; nybble shift
	mov	r7, #15
	mov	r7, r7, lsl r11            ; nybble mask
	ldrb	r5, [r2, r4]               ; erase the old point
	mla	r6, r9, r5, r0
	ldrb	r5, [r6, r4, lsr #1]!
	bic	r5, r5, r7
	strb	r5, [r6]
	mul	r5, r3, r4
	mov	r5, r5, lsr #10            ; (N*nch*2 * x) / 1024
	bic	r5, r5, r10
	ldrb	r5, [r1, r5]
	ldrb	r5, [r8, r5]
	mov	r5, r5, lsl #24
	mov	r5, r5, asr #24            ; signed offset, +-64
	rsb	r5, r5, #SCOPE_MID
	strb	r5, [r2, r4]
	mla	r6, r9, r5, r0
	ldrb	r5, [r6, r4, lsr #1]!
	mov	r7, #SCOPE_COL
	orr	r5, r5, r7, lsl r11
	strb	r5, [r6]
	add	r4, r4, #1
	cmp	r4, #320
	blo	dsc_loop
	ldmfd	sp!, {r0-r11, pc}

print_at:                                  ; VDU 31,r0,r1 then string r2
	stmfd	sp!, {r0, lr}
	swi	OS_WriteI+31
	swi	OS_WriteC
	mov	r0, r1
	swi	OS_WriteC
	mov	r0, r2
	swi	OS_Write0
	ldmfd	sp!, {r0, pc}

print_num:                                 ; r0 unsigned
	stmfd	sp!, {r0-r2, lr}
	adr	r1, numbuf
	mov	r2, #16
	swi	OS_ConvertCardinal4
	swi	OS_Write0
	ldmfd	sp!, {r0-r2, pc}

udiv:                                      ; r0 = r0 / r1 (unsigned); uses r2, r3
	mov	r2, #0
	mov	r3, #1
	cmp	r1, #0
	moveq	r0, #0
	moveq	pc, lr
udiv_up:
	cmp	r1, r0
	movls	r1, r1, lsl #1
	movls	r3, r3, lsl #1
	bls	udiv_up
udiv_down:
	cmp	r0, r1
	subhs	r0, r0, r1
	addhs	r2, r2, r3
	movs	r3, r3, lsr #1
	movne	r1, r1, lsr #1
	bne	udiv_down
	mov	r0, r2
	mov	pc, lr

; ----------------------------------------------------------------------------
; Errors

err_usage:
	adr	r0, s_usage
	b	err_print
err_notfound:
	adr	r0, s_notfound
	b	err_print
err_format:
	adr	r0, s_format
	b	err_print
err_os:
	add	r0, r0, #4
	b	err_print
err_memory:
	adr	r0, s_memory
	swi	OS_Write0
	sub	r0, r3, #0x8000
	mov	r0, r0, lsr #10
	add	r0, r0, #1
	bl	print_num
	adr	r0, s_k
err_print:
	mov	r4, r0
	ldr	r0, menu_mode
	cmp	r0, #0
	beq	err_exit
	mov	r0, #1                     ; menu mode: show it, wait, back to menu
	mov	r1, #MSG_ROW
	mov	r2, r4
	bl	print_at
	mov	r0, #1
	mov	r1, #MSG_ROW+1
	adr	r2, s_presskey
	bl	print_at
	bl	tap_sync
err_wait:
	bl	read_input
	cmp	r0, #0
	beq	err_wait
	b	menu
err_exit:
	bl	restore_escape
	mov	r0, r4
	swi	OS_Write0
	swi	OS_NewLine
	swi	OS_Exit

restore_escape:
	stmfd	sp!, {r0-r2, lr}
	mov	r0, #229
	ldr	r1, old_esc
	mov	r2, #0
	swi	OS_Byte
	ldmfd	sp!, {r0-r2, pc}

; ----------------------------------------------------------------------------
; Data

ulc_magic:	.long	0x32434C55        ; 'ULC2'
adp_magic:	.long	0x34504441        ; 'ADP4'
fmt_adpcm:	.long	0
adp_bits:	.long	4
adp_next:	.long	0
one_million:	.long	1000000
p_file:		.long	file_data
p_outbuf:	.long	outbuf
p_ring:		.long	ring
p_vidc11:	.long	vidc11 + 1024
p_vidc12:	.long	vidc12 + 2048
p_lin11:	.long	lin11_8 + 1024
last_block:	.long	ring
p_ulc_state:	.long	ulc_State
p_lap:		.long	ulc_TransformBuffer + 4*2048
vdu_vars:	.long	149, -1           ; ScreenStart
scr_start:	.long	0

ram_limit:	.long	0
hdr_bs:		.long	0
hdr_nblk:	.long	0
hdr_rate:	.long	0
hdr_nch:	.long	0
hdr_kbps:	.long	0
block_bytes:	.long	0
stream_start:	.long	0
period_us:	.long	0
blocks_left:	.long	0
blocks_played:	.long	0
ring_rd:	.long	0                  ; bytes consumed by the sound interrupt
ring_wr:	.long	0                  ; bytes produced by the decoder
underruns:	.long	0
dec_cs:		.long	0
cpu_pct:	.long	0
win_start:	.long	0
last_draw:	.long	0
last_status:	.long	0
old_config:	.long	0, 0, 0, 0, 0
old_stereo1:	.long	0
old_stereo2:	.long	0
old_enable:	.long	0
dma_samples:	.long	0                  ; DMA size granted (bytes per channel)
stopping:	.long	0
old_esc:	.long	0
menu_mode:	.long	0
mouse_prev:	.long	0
tap_x:		.long	0
tap_y:		.long	0
tap_last:	.long	0
tap_pending:	.long	0
mouse_box:	.byte	1, 0, 0, 0, 0, 0xFF, 0x04, 0xFF, 0x03   ; 0,0 - 1279,1023
.align 4
path_ptr:	.long	0
title_ptr:	.long	0

track_table:	; leaf name, display name, menu line
	.long	t1_leaf, t1_name, t1_line
	.long	t2_leaf, t2_name, t2_line
	.long	t3_leaf, t3_name, t3_line
	.long	t4_leaf, t4_name, t4_line
t1_leaf:	.asciz	"DemoB"
t2_leaf:	.asciz	"DemoC"
t3_leaf:	.asciz	"DemoD"
t4_leaf:	.asciz	"Particle"
t1_name:	.asciz	"ArchieShaderToy demo B"
t2_name:	.asciz	"ArchieShaderToy demo C"
t3_name:	.asciz	"ArchieShaderToy demo D"
t4_name:	.asciz	"Archie Particle demo"
t1_line:	.asciz	"1   ArchieShaderToy demo B   0:33"
t2_line:	.asciz	"2   ArchieShaderToy demo C   0:51"
t3_line:	.asciz	"3   ArchieShaderToy demo D   0:39"
t4_line:	.asciz	"4   Archie Particle demo     1:26"
s_tracks:	.asciz	".Tracks."
s_menu_title:	.asciz	"ULC Player  -  Acorn Archimedes"
s_menu_fmt:	.asciz	"ulc-codec (MDCT), 128 kbps stereo"
s_menu_fmt2:	.asciz	"20.8 kHz, decoded live on the ARM"
s_menu_quit:	.asciz	"Q   Quit"
s_menu_help:	.asciz	"Tap or click a track, or press 1-4."
s_menu_help2:	.asciz	"Tap or press a key to stop playing."
s_loading:	.asciz	"Loading...                "
s_presskey:	.asciz	"Tap or press a key"
.align 4

s_title:	.asciz	"ULC Player  -  Acorn Archimedes"
s_title_adpcm:	.asciz	"IMA ADPCM - Acorn Archimedes"
s_usage:	.asciz	"Usage: player <file.ulc>"
s_notfound:	.asciz	"File not found"
s_format:	.asciz	"Not a ULC2 or ADP4 file this player supports (mono/stereo, N 64..2048)"
s_memory:	.asciz	"Not enough memory: *WimpSlot -min "
s_k:		.asciz	"K"
s_empty:	.asciz	""
s_hz:		.asciz	" Hz  "
s_ch:		.asciz	" ch  N="
s_sp2:		.asciz	"  "
s_kbps:		.asciz	" kbps"
s_cpu:		.asciz	"CPU      "
s_pct:		.asciz	"%   "
s_buffer:	.asciz	"Buffer   "
s_bytes:	.asciz	" bytes   "
s_under:	.asciz	"Underruns "
s_blocks:	.asciz	"Blocks   "
s_pad:		.asciz	"    "
s_escape:	.asciz	"Tap, click or any key to stop"
.align 4
numbuf:		.space	16
fname:		.space	256
argbuf:		.space	256
oldy:		.space	320

.align 4
.include "build/adpcm_dec.inc"          ; ADPCM decoder, from gen_adpcm.py

.include "decoder.asm"
.include "fourier.asm"

.align 4
vidc_table:
	.incbin	"build/vidc_table.bin"     ; 256 bytes: signed 8-bit linear -> VIDC log
scope_table:
	.incbin	"build/scope_table.bin"    ; 256 bytes: VIDC log -> signed scope offset
vidc11:
	.incbin	"build/vidc11.bin"         ; 2 KB: 11-bit linear (from the centre) -> VIDC log
vidc12:
	.incbin	"build/vidc12.bin"         ; 4 KB: 12-bit linear (from the centre) -> VIDC log
lin11_8:
	.incbin	"build/lin11_8.bin"        ; 2 KB: 11-bit -> 8-bit linear (stereo, before M/S)
ima4:
	.incbin	"build/ima4.bin"           ; ADPCM step tables (gen_adpcm.py)
ima3:
	.incbin	"build/ima3.bin"
ima2:
	.incbin	"build/ima2.bin"

.align 4
image_end:
.equ ulc_TransformBuffer, (image_end + 15) & ~15   ; 8 KB transform + 8 KB lapping
.equ outbuf,    ulc_TransformBuffer + 4*4096       ; 2 x 2 KB decoder output
.equ ulc_FFTBuffer, outbuf + 2*CH2_OFFS            ; 8 KB: the DCT-IV's output
.equ ring,      ulc_FFTBuffer + 4*2048             ; 32 KB VIDC ring
.equ file_data, ring + RING_SIZE                   ; the .ulc file is loaded here
