	.file	"meldra.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c"
	.loc	1 106 0                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:106:0
	.cfi_startproc
# %bb.0:
	pushq	%rbp
	.cfi_def_cfa_offset 16
	pushq	%r15
	.cfi_def_cfa_offset 24
	pushq	%r14
	.cfi_def_cfa_offset 32
	pushq	%r13
	.cfi_def_cfa_offset 40
	pushq	%r12
	.cfi_def_cfa_offset 48
	pushq	%rbx
	.cfi_def_cfa_offset 56
	pushq	%rax
	.cfi_def_cfa_offset 64
	.cfi_offset %rbx, -56
	.cfi_offset %r12, -48
	.cfi_offset %r13, -40
	.cfi_offset %r14, -32
	.cfi_offset %r15, -24
	.cfi_offset %rbp, -16
	.loc	1 107 14 prologue_end           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:107:14
	cmpl	$2, %edi
	jne	.LBB0_1
# %bb.2:
	.loc	1 108 59                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:108:59
	movq	8(%rsi), %rdi
	xorl	%ebx, %ebx
	.loc	1 108 50 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:108:50
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$0, %r14d
.Ltmp0:
	.loc	1 64 34 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:64:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	testq	%rax, %rax
	.loc	1 65 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:65:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	je	.LBB0_10
# %bb.3:
	movl	%eax, %ecx
	andl	$3, %ecx
	cmpq	$4, %rax
	jae	.LBB0_5
# %bb.4:
	.loc	1 0 9 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:0:9
	movl	$1, %edx
	xorl	%r14d, %r14d
	xorl	%esi, %esi
	.loc	1 65 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:65:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	jmp	.LBB0_8
.Ltmp1:
.LBB0_1:
	.loc	1 107 62 is_stmt 1              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:107:62
	movq	stderr(%rip), %rcx
	.loc	1 107 22 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:107:22
	movl	$.L.str, %edi
	movl	$29, %esi
	movl	$1, %edx
	callq	fwrite@PLT
	movl	$2, %ebx
	.loc	1 107 71                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:107:71
	jmp	.LBB0_11
.LBB0_5:
.Ltmp2:
	.loc	1 65 9 is_stmt 1                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:65:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	andq	$-4, %rax
	movl	$1, %edx
	xorl	%r14d, %r14d
	movabsq	$2770643475625, %rdi            # imm = 0x28517385CA9
	movabsq	$1687669940693299, %r8          # imm = 0x5FEED47502933
	movabsq	$4611805331264703125, %r9       # imm = 0x40006C83AF490A95
	movabsq	$5263708829673912043, %r10      # imm = 0x490C734AD1CCF6EB
	movabsq	$296701739740555153, %r11       # imm = 0x41E18A90979E791
	movabsq	$-1306000561438895308, %r15     # imm = 0xEDE02768AAF95334
	movabsq	$-1306000561438895305, %r12     # imm = 0xEDE02768AAF95337
	xorl	%esi, %esi
	.loc	1 0 9 is_stmt 0                 # :0:9
.Ltmp3:
	.p2align	4
.LBB0_6:                                # =>This Inner Loop Header: Depth=1
	.loc	1 72 40 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:72:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	imulq	$1664525, %rdx, %r13            # imm = 0x19660D
	.loc	1 88 40                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:88:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	addq	%rsi, %r13
	addq	$1013904223, %r13               # imm = 0x3C6EF35F
	xorq	%r14, %r13
	movq	%rdx, %rbp
	imulq	%rdi, %rbp
	addq	%rsi, %rbp
	addq	%r8, %rbp
	xorq	%r13, %rbp
	movq	%rdx, %r13
	imulq	%r9, %r13
	addq	%rsi, %r13
	addq	%r10, %r13
	.loc	1 76 40                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:76:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	imulq	%r11, %rdx
	.loc	1 88 40                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:88:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	leaq	(%rsi,%r12), %r14
	addq	%rdx, %r14
	.loc	1 76 40                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:76:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	addq	%r15, %rdx
	.loc	1 88 40                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:88:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	xorq	%r13, %r14
	xorq	%rbp, %r14
	.loc	1 96 40                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:96:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	addq	$4, %rsi
	.loc	1 65 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:65:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	cmpq	%rsi, %rax
	jne	.LBB0_6
# %bb.7:
	testq	%rcx, %rcx
	je	.LBB0_10
.LBB0_8:
	addq	$1013904223, %rsi               # imm = 0x3C6EF35F
	.loc	1 0 9 is_stmt 0                 # :0:9
.Ltmp4:
	.p2align	4
.LBB0_9:                                # =>This Inner Loop Header: Depth=1
	.loc	1 72 40 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:72:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	imulq	$1664525, %rdx, %rdx            # imm = 0x19660D
	.loc	1 88 40                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:88:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	leaq	(%rsi,%rdx), %rax
	.loc	1 76 40                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:76:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	addq	$1013904223, %rdx               # imm = 0x3C6EF35F
	.loc	1 88 40                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:88:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	xorq	%rax, %r14
	.loc	1 65 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:65:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:109:23 ]
	incq	%rsi
	decq	%rcx
	jne	.LBB0_9
.Ltmp5:
.LBB0_10:
	.loc	1 110 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:110:13
	movq	stderr(%rip), %rdi
	.loc	1 110 5 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:110:5
	movl	$.L.str.1, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 111 5 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:111:5
	movl	$.L.str.2, %edi
	movq	%r14, %rsi
	xorl	%eax, %eax
	callq	printf
.LBB0_11:
	.loc	1 113 1                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:113:1
	movl	%ebx, %eax
	.loc	1 113 1 epilogue_begin is_stmt 0 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c:113:1
	addq	$8, %rsp
	.cfi_def_cfa_offset 56
	popq	%rbx
	.cfi_def_cfa_offset 48
	popq	%r12
	.cfi_def_cfa_offset 40
	popq	%r13
	.cfi_def_cfa_offset 32
	popq	%r14
	.cfi_def_cfa_offset 24
	popq	%r15
	.cfi_def_cfa_offset 16
	popq	%rbp
	.cfi_def_cfa_offset 8
	retq
.Ltmp6:
.Lfunc_end0:
	.size	main, .Lfunc_end0-main
	.cfi_endproc
                                        # -- End function
	.type	.L.str,@object                  # @.str
	.section	.rodata.str1.1,"aMS",@progbits,1
.L.str:
	.asciz	"invalid entry argument count\n"
	.size	.L.str, 30

	.type	.L.str.1,@object                # @.str.1
.L.str.1:
	.asciz	"MELDRA_ALLOCATIONS=%lu\n"
	.size	.L.str.1, 24

	.type	.L.str.2,@object                # @.str.2
.L.str.2:
	.asciz	"%lu\n"
	.size	.L.str.2, 5

	.section	.debug_abbrev,"",@progbits
	.byte	1                               # Abbreviation Code
	.byte	17                              # DW_TAG_compile_unit
	.byte	1                               # DW_CHILDREN_yes
	.byte	37                              # DW_AT_producer
	.byte	14                              # DW_FORM_strp
	.byte	19                              # DW_AT_language
	.byte	5                               # DW_FORM_data2
	.byte	3                               # DW_AT_name
	.byte	14                              # DW_FORM_strp
	.byte	16                              # DW_AT_stmt_list
	.byte	23                              # DW_FORM_sec_offset
	.byte	27                              # DW_AT_comp_dir
	.byte	14                              # DW_FORM_strp
	.byte	17                              # DW_AT_low_pc
	.byte	1                               # DW_FORM_addr
	.byte	18                              # DW_AT_high_pc
	.byte	6                               # DW_FORM_data4
	.byte	0                               # EOM(1)
	.byte	0                               # EOM(2)
	.byte	2                               # Abbreviation Code
	.byte	46                              # DW_TAG_subprogram
	.byte	0                               # DW_CHILDREN_no
	.byte	3                               # DW_AT_name
	.byte	14                              # DW_FORM_strp
	.byte	32                              # DW_AT_inline
	.byte	11                              # DW_FORM_data1
	.byte	0                               # EOM(1)
	.byte	0                               # EOM(2)
	.byte	3                               # Abbreviation Code
	.byte	46                              # DW_TAG_subprogram
	.byte	1                               # DW_CHILDREN_yes
	.byte	17                              # DW_AT_low_pc
	.byte	1                               # DW_FORM_addr
	.byte	18                              # DW_AT_high_pc
	.byte	6                               # DW_FORM_data4
	.byte	3                               # DW_AT_name
	.byte	14                              # DW_FORM_strp
	.byte	0                               # EOM(1)
	.byte	0                               # EOM(2)
	.byte	4                               # Abbreviation Code
	.byte	29                              # DW_TAG_inlined_subroutine
	.byte	0                               # DW_CHILDREN_no
	.byte	49                              # DW_AT_abstract_origin
	.byte	19                              # DW_FORM_ref4
	.byte	85                              # DW_AT_ranges
	.byte	23                              # DW_FORM_sec_offset
	.byte	88                              # DW_AT_call_file
	.byte	11                              # DW_FORM_data1
	.byte	89                              # DW_AT_call_line
	.byte	11                              # DW_FORM_data1
	.byte	87                              # DW_AT_call_column
	.byte	11                              # DW_FORM_data1
	.byte	0                               # EOM(1)
	.byte	0                               # EOM(2)
	.byte	0                               # EOM(3)
	.section	.debug_info,"",@progbits
.Lcu_begin0:
	.long	.Ldebug_info_end0-.Ldebug_info_start0 # Length of Unit
.Ldebug_info_start0:
	.short	4                               # DWARF version number
	.long	.debug_abbrev                   # Offset Into Abbrev. Section
	.byte	8                               # Address Size (in bytes)
	.byte	1                               # Abbrev [1] 0xb:0x44 DW_TAG_compile_unit
	.long	.Linfo_string0                  # DW_AT_producer
	.short	29                              # DW_AT_language
	.long	.Linfo_string1                  # DW_AT_name
	.long	.Lline_table_start0             # DW_AT_stmt_list
	.long	.Linfo_string2                  # DW_AT_comp_dir
	.quad	.Lfunc_begin0                   # DW_AT_low_pc
	.long	.Lfunc_end0-.Lfunc_begin0       # DW_AT_high_pc
	.byte	2                               # Abbrev [2] 0x2a:0x6 DW_TAG_subprogram
	.long	.Linfo_string3                  # DW_AT_name
	.byte	1                               # DW_AT_inline
	.byte	3                               # Abbrev [3] 0x30:0x1e DW_TAG_subprogram
	.quad	.Lfunc_begin0                   # DW_AT_low_pc
	.long	.Lfunc_end0-.Lfunc_begin0       # DW_AT_high_pc
	.long	.Linfo_string4                  # DW_AT_name
	.byte	4                               # Abbrev [4] 0x41:0xc DW_TAG_inlined_subroutine
	.long	42                              # DW_AT_abstract_origin
	.long	.Ldebug_ranges0                 # DW_AT_ranges
	.byte	1                               # DW_AT_call_file
	.byte	109                             # DW_AT_call_line
	.byte	23                              # DW_AT_call_column
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
.Ldebug_info_end0:
	.section	.debug_ranges,"",@progbits
.Ldebug_ranges0:
	.quad	.Ltmp0-.Lfunc_begin0
	.quad	.Ltmp1-.Lfunc_begin0
	.quad	.Ltmp2-.Lfunc_begin0
	.quad	.Ltmp5-.Lfunc_begin0
	.quad	0
	.quad	0
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/meldra.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=53
.Linfo_string3:
	.asciz	"meldra_fn_main"                # string offset=100
.Linfo_string4:
	.asciz	"main"                          # string offset=115
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
