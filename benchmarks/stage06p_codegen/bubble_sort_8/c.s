	.file	"c.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "benchmarks/stage06p_codegen/bubble_sort_8/c.c"
	.loc	1 24 0                          # benchmarks/stage06p_codegen/bubble_sort_8/c.c:24:0
	.cfi_startproc
# %bb.0:
	movl	$2, %eax
.Ltmp0:
	.loc	1 25 14 prologue_end            # benchmarks/stage06p_codegen/bubble_sort_8/c.c:25:14
	cmpl	$2, %edi
	je	.LBB0_1
# %bb.6:
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # benchmarks/stage06p_codegen/bubble_sort_8/c.c:30:1
	retq
.LBB0_1:
	.loc	1 0 1 is_stmt 0                 # benchmarks/stage06p_codegen/bubble_sort_8/c.c:0:1
	pushq	%r15
	.cfi_def_cfa_offset 16
	pushq	%r14
	.cfi_def_cfa_offset 24
	pushq	%rbx
	.cfi_def_cfa_offset 32
	.cfi_offset %rbx, -32
	.cfi_offset %r14, -24
	.cfi_offset %r15, -16
	.loc	1 26 36 is_stmt 1               # benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:36
	movq	8(%rsi), %rdi
	.loc	1 26 27 is_stmt 0               # benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:27
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$1183, %ebx                     # imm = 0x49F
.Ltmp1:
	.loc	1 21 36 is_stmt 1               # benchmarks/stage06p_codegen/bubble_sort_8/c.c:21:36 @[ benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:23 ]
	testq	%rax, %rax
	.loc	1 21 5 is_stmt 0                # benchmarks/stage06p_codegen/bubble_sort_8/c.c:21:5 @[ benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:23 ]
	je	.LBB0_5
# %bb.2:
	.loc	1 0 5                           # benchmarks/stage06p_codegen/bubble_sort_8/c.c:0:5
	movl	$9, %ebx
	movl	$1, %r9d
	movl	$8, %r8d
	movl	$2, %r10d
	movl	$7, %edi
	movl	$3, %esi
	movl	$6, %edx
	movl	$4, %ecx
	.p2align	4
.LBB0_3:                                # =>This Inner Loop Header: Depth=1
	.loc	1 21 166 is_stmt 1              # benchmarks/stage06p_codegen/bubble_sort_8/c.c:21:166 @[ benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:23 ]
	cmpq	%r9, %rbx
	movq	%r9, %r11
	cmovaq	%rbx, %r11
	cmovbq	%rbx, %r9
	cmpq	%r8, %r11
	movq	%r8, %rbx
	cmovaq	%r11, %rbx
	cmovaeq	%r8, %r11
	cmpq	%r10, %rbx
	movq	%r10, %r14
	cmovaq	%rbx, %r14
	cmovaeq	%r10, %rbx
	cmpq	%rdi, %r14
	movq	%rdi, %r10
	cmovaq	%r14, %r10
	cmovaeq	%rdi, %r14
	cmpq	%rsi, %r10
	movq	%rsi, %r15
	cmovaq	%r10, %r15
	cmovaeq	%rsi, %r10
	cmpq	%rdx, %r15
	movq	%rdx, %rsi
	cmovaq	%r15, %rsi
	.loc	1 20 14                         # benchmarks/stage06p_codegen/bubble_sort_8/c.c:20:14 @[ benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:23 ]
	movq	%rcx, %rdi
	.loc	1 21 166                        # benchmarks/stage06p_codegen/bubble_sort_8/c.c:21:166 @[ benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:23 ]
	cmovaeq	%rdx, %r15
	cmpq	%rcx, %rsi
	cmovaq	%rsi, %rcx
	cmovaeq	%rdi, %rsi
	cmpq	%r9, %r8
	movq	%r11, %rdi
	cmovbq	%r9, %rdi
	cmovaeq	%r9, %r11
	cmpq	%rbx, %rdi
	movq	%rbx, %r8
	cmovaq	%rdi, %r8
	cmovaeq	%rbx, %rdi
	cmpq	%r14, %r8
	movq	%r14, %rbx
	cmovaq	%r8, %rbx
	cmovaeq	%r14, %r8
	cmpq	%r10, %rbx
	movq	%r10, %r14
	cmovaq	%rbx, %r14
	cmovaeq	%r10, %rbx
	cmpq	%r15, %r14
	movq	%r15, %r10
	cmovaq	%r14, %r10
	cmovaeq	%r15, %r14
	cmpq	%rsi, %r10
	movq	%rsi, %rdx
	cmovaq	%r10, %rdx
	cmovaeq	%rsi, %r10
	cmpq	%rdi, %r11
	movq	%rdi, %r9
	cmovaq	%r11, %r9
	cmovbq	%r11, %rdi
	cmpq	%r8, %r9
	movq	%r8, %r11
	cmovaq	%r9, %r11
	cmovaeq	%r8, %r9
	cmpq	%rbx, %r11
	movq	%rbx, %r15
	cmovaq	%r11, %r15
	cmovaeq	%rbx, %r11
	cmpq	%r14, %r15
	movq	%r14, %rbx
	cmovaq	%r15, %rbx
	cmovaeq	%r14, %r15
	cmpq	%r10, %rbx
	movq	%r10, %rsi
	cmovaq	%rbx, %rsi
	cmovaeq	%r10, %rbx
	cmpq	%rdi, %r8
	movq	%r9, %r8
	cmovbq	%rdi, %r8
	cmovaeq	%rdi, %r9
	cmpq	%r11, %r8
	movq	%r11, %r14
	cmovaq	%r8, %r14
	cmovaeq	%r11, %r8
	cmpq	%r15, %r14
	movq	%r15, %r11
	cmovaq	%r14, %r11
	cmovaeq	%r15, %r14
	cmpq	%rbx, %r11
	movq	%rbx, %rdi
	cmovaq	%r11, %rdi
	cmovaeq	%rbx, %r11
	cmpq	%r8, %r9
	movq	%r8, %r15
	cmovaq	%r9, %r15
	cmovbq	%r9, %r8
	cmpq	%r14, %r15
	movq	%r14, %r9
	cmovaq	%r15, %r9
	cmovaeq	%r14, %r15
	cmpq	%r11, %r9
	movq	%r11, %r10
	cmovaq	%r9, %r10
	cmovaeq	%r11, %r9
	cmpq	%r8, %r14
	movq	%r15, %rbx
	cmovbq	%r8, %rbx
	cmovaeq	%r8, %r15
	cmpq	%r9, %rbx
	movq	%r9, %r8
	cmovaq	%rbx, %r8
	cmovaeq	%r9, %rbx
	cmpq	%rbx, %r15
	movq	%rbx, %r9
	cmovaq	%r15, %r9
	cmovbq	%r15, %rbx
	.loc	1 21 36 is_stmt 0               # benchmarks/stage06p_codegen/bubble_sort_8/c.c:21:36 @[ benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:23 ]
	decq	%rax
	.loc	1 21 5                          # benchmarks/stage06p_codegen/bubble_sort_8/c.c:21:5 @[ benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:23 ]
	jne	.LBB0_3
# %bb.4:
	.loc	1 22 22 is_stmt 1               # benchmarks/stage06p_codegen/bubble_sort_8/c.c:22:22 @[ benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:23 ]
	imulq	$131, %rbx, %rbx
	.loc	1 22 12 is_stmt 0               # benchmarks/stage06p_codegen/bubble_sort_8/c.c:22:12 @[ benchmarks/stage06p_codegen/bubble_sort_8/c.c:26:23 ]
	addq	%rcx, %rbx
.Ltmp2:
.LBB0_5:
	.loc	1 27 13 is_stmt 1               # benchmarks/stage06p_codegen/bubble_sort_8/c.c:27:13
	movq	stderr(%rip), %rdi
	.loc	1 27 5 is_stmt 0                # benchmarks/stage06p_codegen/bubble_sort_8/c.c:27:5
	movl	$.L.str, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 28 5 is_stmt 1                # benchmarks/stage06p_codegen/bubble_sort_8/c.c:28:5
	movl	$.L.str.1, %edi
	movq	%rbx, %rsi
	xorl	%eax, %eax
	callq	printf
	xorl	%eax, %eax
	popq	%rbx
	.cfi_def_cfa_offset 24
	popq	%r14
	.cfi_def_cfa_offset 16
	popq	%r15
	.cfi_def_cfa_offset 8
	.cfi_restore %rbx
	.cfi_restore %r14
	.cfi_restore %r15
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # benchmarks/stage06p_codegen/bubble_sort_8/c.c:30:1
	retq
.Ltmp3:
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
	.long	.Ltmp2-.Ltmp1                   # DW_AT_high_pc
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
	.asciz	"benchmarks/stage06p_codegen/bubble_sort_8/c.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=47
.Linfo_string3:
	.asciz	"run"                           # string offset=94
.Linfo_string4:
	.asciz	"main"                          # string offset=98
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
