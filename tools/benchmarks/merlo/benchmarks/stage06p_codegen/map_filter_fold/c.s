	.file	"c.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c"
	.loc	1 24 0                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:24:0
	.cfi_startproc
# %bb.0:
	movl	$2, %eax
.Ltmp0:
	.loc	1 25 14 prologue_end            # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:25:14
	cmpl	$2, %edi
	je	.LBB0_1
# %bb.5:
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:30:1
	retq
.LBB0_1:
	.loc	1 0 1 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:0:1
	pushq	%rbx
	.cfi_def_cfa_offset 16
	subq	$64, %rsp
	.cfi_def_cfa_offset 80
	.cfi_offset %rbx, -16
	.loc	1 26 36 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:36
	movq	8(%rsi), %rdi
	.loc	1 26 27 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:27
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
.Ltmp1:
	.loc	1 20 14 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:20:14 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	movaps	.L__const.run.values+48(%rip), %xmm0
	movaps	%xmm0, 48(%rsp)
	movaps	.L__const.run.values+32(%rip), %xmm0
	movaps	%xmm0, 32(%rsp)
	movaps	.L__const.run.values+16(%rip), %xmm0
	movaps	%xmm0, 16(%rsp)
	movaps	.L__const.run.values(%rip), %xmm0
	movaps	%xmm0, (%rsp)
	movl	$0, %ebx
	.loc	1 21 36                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	testq	%rax, %rax
	.loc	1 21 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	je	.LBB0_4
# %bb.2:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:0:5
	xorl	%ecx, %ecx
	xorl	%ebx, %ebx
	xorl	%edx, %edx
	.p2align	4
.LBB0_3:                                # =>This Inner Loop Header: Depth=1
	.loc	1 21 65 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:65 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	movl	%edx, %esi
	andl	$7, %esi
	.loc	1 21 70 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:70 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	addq	%rdx, (%rsp,%rsi,8)
	.loc	1 21 133                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:133 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	movq	(%rsp), %rdi
	movq	8(%rsp), %r8
	.loc	1 21 143                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:143 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	imulq	%rdi, %rdi
	.loc	1 21 171                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:171 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	testb	$1, %dil
	cmovneq	%rcx, %rdi
	addq	%rbx, %rdi
	.loc	1 21 143                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:143 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	imulq	%r8, %r8
	.loc	1 21 171                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:171 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	testb	$1, %r8b
	cmovneq	%rcx, %r8
	.loc	1 21 133                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:133 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	movq	16(%rsp), %rsi
	.loc	1 21 143                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:143 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	imulq	%rsi, %rsi
	.loc	1 21 171                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:171 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	testb	$1, %sil
	cmovneq	%rcx, %rsi
	addq	%r8, %rsi
	addq	%rdi, %rsi
	.loc	1 21 133                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:133 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	movq	24(%rsp), %rdi
	.loc	1 21 143                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:143 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	imulq	%rdi, %rdi
	.loc	1 21 171                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:171 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	testb	$1, %dil
	cmovneq	%rcx, %rdi
	.loc	1 21 133                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:133 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	movq	32(%rsp), %r8
	.loc	1 21 143                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:143 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	imulq	%r8, %r8
	.loc	1 21 171                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:171 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	testb	$1, %r8b
	cmovneq	%rcx, %r8
	addq	%rdi, %r8
	.loc	1 21 133                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:133 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	movq	40(%rsp), %rdi
	.loc	1 21 143                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:143 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	imulq	%rdi, %rdi
	.loc	1 21 171                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:171 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	testb	$1, %dil
	cmovneq	%rcx, %rdi
	addq	%r8, %rdi
	addq	%rsi, %rdi
	.loc	1 21 133                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:133 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	movq	48(%rsp), %rsi
	.loc	1 21 143                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:143 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	imulq	%rsi, %rsi
	.loc	1 21 171                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:171 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	testb	$1, %sil
	cmovneq	%rcx, %rsi
	.loc	1 21 133                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:133 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	movq	56(%rsp), %rbx
	.loc	1 21 143                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:143 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	imulq	%rbx, %rbx
	.loc	1 21 171                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:171 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	testb	$1, %bl
	cmovneq	%rcx, %rbx
	addq	%rsi, %rbx
	addq	%rdi, %rbx
	.loc	1 21 41                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:41 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	incq	%rdx
	.loc	1 21 36                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	cmpq	%rdx, %rax
	.loc	1 21 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:26:23 ]
	jne	.LBB0_3
.Ltmp2:
.LBB0_4:
	.loc	1 27 13 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:27:13
	movq	stderr(%rip), %rdi
	.loc	1 27 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:27:5
	movl	$.L.str, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 28 5 is_stmt 1                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:28:5
	movl	$.L.str.1, %edi
	movq	%rbx, %rsi
	xorl	%eax, %eax
	callq	printf
	xorl	%eax, %eax
	addq	$64, %rsp
	.cfi_def_cfa_offset 16
	popq	%rbx
	.cfi_def_cfa_offset 8
	.cfi_restore %rbx
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c:30:1
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

	.type	.L__const.run.values,@object    # @__const.run.values
	.section	.rodata,"a",@progbits
	.p2align	4, 0x0
.L__const.run.values:
	.quad	1                               # 0x1
	.quad	2                               # 0x2
	.quad	3                               # 0x3
	.quad	4                               # 0x4
	.quad	5                               # 0x5
	.quad	6                               # 0x6
	.quad	7                               # 0x7
	.quad	8                               # 0x8
	.size	.L__const.run.values, 64

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
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/c.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=49
.Linfo_string3:
	.asciz	"run"                           # string offset=96
.Linfo_string4:
	.asciz	"main"                          # string offset=100
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
