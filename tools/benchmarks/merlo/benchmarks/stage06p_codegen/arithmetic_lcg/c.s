	.file	"c.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c"
	.loc	1 24 0                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:24:0
	.cfi_startproc
# %bb.0:
	movl	$2, %eax
.Ltmp0:
	.loc	1 25 14 prologue_end            # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:25:14
	cmpl	$2, %edi
	je	.LBB0_1
# %bb.10:
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:30:1
	retq
.LBB0_1:
	.loc	1 0 1 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:0:1
	pushq	%r15
	.cfi_def_cfa_offset 16
	pushq	%r14
	.cfi_def_cfa_offset 24
	pushq	%r13
	.cfi_def_cfa_offset 32
	pushq	%r12
	.cfi_def_cfa_offset 40
	pushq	%rbx
	.cfi_def_cfa_offset 48
	.cfi_offset %rbx, -48
	.cfi_offset %r12, -40
	.cfi_offset %r13, -32
	.cfi_offset %r14, -24
	.cfi_offset %r15, -16
	.loc	1 26 36 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:36
	movq	8(%rsi), %rdi
	.loc	1 26 27 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:27
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$0, %ebx
.Ltmp1:
	.loc	1 21 28 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	testq	%rax, %rax
	.loc	1 21 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	je	.LBB0_9
# %bb.2:
	movl	%eax, %ecx
	andl	$3, %ecx
	cmpq	$4, %rax
	jae	.LBB0_4
# %bb.3:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:0:5
	movl	$1, %edx
	xorl	%esi, %esi
	xorl	%ebx, %ebx
	.loc	1 21 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	jmp	.LBB0_7
.LBB0_4:
	andq	$-4, %rax
	movl	$1, %edx
	xorl	%esi, %esi
	movabsq	$2770643475625, %rdi            # imm = 0x28517385CA9
	movabsq	$1687669940693299, %r8          # imm = 0x5FEED47502933
	movabsq	$4611805331264703125, %r9       # imm = 0x40006C83AF490A95
	movabsq	$5263708829673912043, %r10      # imm = 0x490C734AD1CCF6EB
	movabsq	$296701739740555153, %r11       # imm = 0x41E18A90979E791
	movabsq	$-1306000561438895308, %r14     # imm = 0xEDE02768AAF95334
	movabsq	$-1306000561438895305, %r15     # imm = 0xEDE02768AAF95337
	xorl	%ebx, %ebx
	.loc	1 0 5                           # :0:5
.Ltmp2:
	.p2align	4
.LBB0_5:                                # =>This Inner Loop Header: Depth=1
	.loc	1 21 54                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:54 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	imulq	$1664525, %rdx, %r12            # imm = 0x19660D
	.loc	1 21 107                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:107 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	addq	%rsi, %r12
	addq	$1013904223, %r12               # imm = 0x3C6EF35F
	xorq	%rbx, %r12
	movq	%rdx, %r13
	imulq	%rdi, %r13
	addq	%rsi, %r13
	addq	%r8, %r13
	xorq	%r12, %r13
	movq	%rdx, %r12
	imulq	%r9, %r12
	addq	%rsi, %r12
	addq	%r10, %r12
	.loc	1 21 74                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:74 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	imulq	%r11, %rdx
	.loc	1 21 107                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:107 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	leaq	(%rsi,%r15), %rbx
	addq	%rdx, %rbx
	.loc	1 21 74                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:74 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	addq	%r14, %rdx
	.loc	1 21 107                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:107 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	xorq	%r12, %rbx
	xorq	%r13, %rbx
	.loc	1 21 33                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:33 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	addq	$4, %rsi
	.loc	1 21 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	cmpq	%rsi, %rax
	jne	.LBB0_5
# %bb.6:
	testq	%rcx, %rcx
	je	.LBB0_9
.LBB0_7:
	addq	$1013904223, %rsi               # imm = 0x3C6EF35F
	.loc	1 0 5                           # :0:5
.Ltmp3:
	.p2align	4
.LBB0_8:                                # =>This Inner Loop Header: Depth=1
	.loc	1 21 54                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:54 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	imulq	$1664525, %rdx, %rdx            # imm = 0x19660D
	.loc	1 21 107                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:107 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	leaq	(%rsi,%rdx), %rax
	.loc	1 21 74                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:74 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	addq	$1013904223, %rdx               # imm = 0x3C6EF35F
	.loc	1 21 107                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:107 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	xorq	%rax, %rbx
	.loc	1 21 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:26:23 ]
	incq	%rsi
	decq	%rcx
	jne	.LBB0_8
.Ltmp4:
.LBB0_9:
	.loc	1 27 13 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:27:13
	movq	stderr(%rip), %rdi
	.loc	1 27 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:27:5
	movl	$.L.str, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 28 5 is_stmt 1                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:28:5
	movl	$.L.str.1, %edi
	movq	%rbx, %rsi
	xorl	%eax, %eax
	callq	printf
	xorl	%eax, %eax
	popq	%rbx
	.cfi_def_cfa_offset 40
	popq	%r12
	.cfi_def_cfa_offset 32
	popq	%r13
	.cfi_def_cfa_offset 24
	popq	%r14
	.cfi_def_cfa_offset 16
	popq	%r15
	.cfi_def_cfa_offset 8
	.cfi_restore %rbx
	.cfi_restore %r12
	.cfi_restore %r13
	.cfi_restore %r14
	.cfi_restore %r15
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c:30:1
	retq
.Ltmp5:
.Lfunc_end0:
	.size	main, .Lfunc_end0-main
	.cfi_endproc
                                        # -- End function
	.type	.L.str,@object                  # @.str
	.section	.rodata.str1.1,"aMS",@progbits,1
.L.str:
	.asciz	"BENCH_ALLOCATIONS=%lu\n"
	.size	.L.str, 23

	.type	.L.str.1,@object                # @.str.1
.L.str.1:
	.asciz	"%lu\n"
	.size	.L.str.1, 5

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
	.byte	17                              # DW_AT_low_pc
	.byte	1                               # DW_FORM_addr
	.byte	18                              # DW_AT_high_pc
	.byte	6                               # DW_FORM_data4
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
	.byte	1                               # Abbrev [1] 0xb:0x4c DW_TAG_compile_unit
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
	.byte	3                               # Abbrev [3] 0x30:0x26 DW_TAG_subprogram
	.quad	.Lfunc_begin0                   # DW_AT_low_pc
	.long	.Lfunc_end0-.Lfunc_begin0       # DW_AT_high_pc
	.long	.Linfo_string4                  # DW_AT_name
	.byte	4                               # Abbrev [4] 0x41:0x14 DW_TAG_inlined_subroutine
	.long	42                              # DW_AT_abstract_origin
	.quad	.Ltmp1                          # DW_AT_low_pc
	.long	.Ltmp4-.Ltmp1                   # DW_AT_high_pc
	.byte	1                               # DW_AT_call_file
	.byte	26                              # DW_AT_call_line
	.byte	23                              # DW_AT_call_column
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
.Ldebug_info_end0:
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/arithmetic_lcg/c.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=48
.Linfo_string3:
	.asciz	"run"                           # string offset=95
.Linfo_string4:
	.asciz	"main"                          # string offset=99
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
