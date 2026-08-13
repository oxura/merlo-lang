	.file	"c.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "benchmarks/stage06p_codegen/fixed_array_scan/c.c"
	.loc	1 24 0                          # benchmarks/stage06p_codegen/fixed_array_scan/c.c:24:0
	.cfi_startproc
# %bb.0:
	movl	$2, %eax
.Ltmp0:
	.loc	1 25 14 prologue_end            # benchmarks/stage06p_codegen/fixed_array_scan/c.c:25:14
	cmpl	$2, %edi
	je	.LBB0_1
# %bb.12:
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # benchmarks/stage06p_codegen/fixed_array_scan/c.c:30:1
	retq
.LBB0_1:
	.loc	1 0 1 is_stmt 0                 # benchmarks/stage06p_codegen/fixed_array_scan/c.c:0:1
	pushq	%rbx
	.cfi_def_cfa_offset 16
	.cfi_offset %rbx, -16
	.loc	1 26 36 is_stmt 1               # benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:36
	movq	8(%rsi), %rdi
	.loc	1 26 27 is_stmt 0               # benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:27
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$0, %ebx
.Ltmp1:
	.loc	1 21 28 is_stmt 1               # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:28 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	testq	%rax, %rax
	.loc	1 21 5 is_stmt 0                # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:5 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	je	.LBB0_11
# %bb.2:
	leaq	-9(%rax), %rcx
	cmpq	$-5, %rcx
	jae	.LBB0_4
# %bb.3:
	.loc	1 0 5                           # benchmarks/stage06p_codegen/fixed_array_scan/c.c:0:5
	xorl	%edx, %edx
	xorl	%ebx, %ebx
	.loc	1 21 5                          # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:5 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	jmp	.LBB0_5
.LBB0_4:
	.loc	1 0 5                           # benchmarks/stage06p_codegen/fixed_array_scan/c.c:0:5
	movl	%eax, %edx
	andl	$12, %edx
	.loc	1 21 5                          # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:5 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	cmpq	$4, %rdx
	movl	$21, %ecx
	movl	$162, %ebx
	cmoveq	%rcx, %rbx
	cmpq	%rdx, %rax
	je	.LBB0_11
.LBB0_5:
	movq	%rax, %rsi
	andq	$3, %rsi
	jne	.LBB0_7
# %bb.6:
	.loc	1 0 5                           # benchmarks/stage06p_codegen/fixed_array_scan/c.c:0:5
	movq	%rdx, %rcx
	.loc	1 21 5                          # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:5 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	jmp	.LBB0_9
.LBB0_7:
	movl	%edx, %edi
	andl	$7, %edi
	xorl	%r8d, %r8d
	shll	$3, %edi
	movq	%rdx, %rcx
	.loc	1 0 5                           # :0:5
.Ltmp2:
	.p2align	4
.LBB0_8:                                # =>This Inner Loop Header: Depth=1
	.loc	1 21 69                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:69 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	incq	%rcx
	movq	.L__const.run.values(%rdi,%r8,8), %r9
	.loc	1 21 64                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:64 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	imulq	%rcx, %r9
	.loc	1 21 47                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:47 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	addq	%r9, %rbx
	.loc	1 21 5                          # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:5 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	incq	%r8
	cmpq	%r8, %rsi
	jne	.LBB0_8
.LBB0_9:
	subq	%rax, %rdx
	cmpq	$-4, %rdx
	ja	.LBB0_11
	.loc	1 0 5                           # :0:5
.Ltmp3:
	.p2align	4
.LBB0_10:                               # =>This Inner Loop Header: Depth=1
	.loc	1 21 59                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:59 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	movl	%ecx, %edx
	andl	$7, %edx
	.loc	1 21 64                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:64 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	leaq	1(%rcx), %rsi
	movq	.L__const.run.values(,%rdx,8), %rdx
	imulq	%rsi, %rdx
	.loc	1 21 47                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:47 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	addq	%rbx, %rdx
	.loc	1 21 59                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:59 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	andl	$7, %esi
	.loc	1 21 64                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:64 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	leaq	2(%rcx), %rdi
	movq	.L__const.run.values(,%rsi,8), %rsi
	imulq	%rdi, %rsi
	.loc	1 21 47                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:47 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	addq	%rdx, %rsi
	.loc	1 21 59                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:59 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	andl	$7, %edi
	.loc	1 21 64                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:64 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	leaq	3(%rcx), %rdx
	movq	.L__const.run.values(,%rdi,8), %rdi
	imulq	%rdx, %rdi
	.loc	1 21 59                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:59 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	andl	$7, %edx
	.loc	1 21 69                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:69 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	addq	$4, %rcx
	movq	.L__const.run.values(,%rdx,8), %rbx
	.loc	1 21 64                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:64 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	imulq	%rcx, %rbx
	.loc	1 21 47                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:47 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	addq	%rdi, %rbx
	addq	%rsi, %rbx
	.loc	1 21 28                         # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:28 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	cmpq	%rcx, %rax
	.loc	1 21 5                          # benchmarks/stage06p_codegen/fixed_array_scan/c.c:21:5 @[ benchmarks/stage06p_codegen/fixed_array_scan/c.c:26:23 ]
	jne	.LBB0_10
.Ltmp4:
.LBB0_11:
	.loc	1 27 13 is_stmt 1               # benchmarks/stage06p_codegen/fixed_array_scan/c.c:27:13
	movq	stderr(%rip), %rdi
	.loc	1 27 5 is_stmt 0                # benchmarks/stage06p_codegen/fixed_array_scan/c.c:27:5
	movl	$.L.str, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 28 5 is_stmt 1                # benchmarks/stage06p_codegen/fixed_array_scan/c.c:28:5
	movl	$.L.str.1, %edi
	movq	%rbx, %rsi
	xorl	%eax, %eax
	callq	printf
	xorl	%eax, %eax
	popq	%rbx
	.cfi_def_cfa_offset 8
	.cfi_restore %rbx
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # benchmarks/stage06p_codegen/fixed_array_scan/c.c:30:1
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

	.type	.L__const.run.values,@object    # @__const.run.values
	.section	.rodata,"a",@progbits
	.p2align	4, 0x0
.L__const.run.values:
	.quad	3                               # 0x3
	.quad	1                               # 0x1
	.quad	4                               # 0x4
	.quad	1                               # 0x1
	.quad	5                               # 0x5
	.quad	9                               # 0x9
	.quad	2                               # 0x2
	.quad	6                               # 0x6
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
	.asciz	"benchmarks/stage06p_codegen/fixed_array_scan/c.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=50
.Linfo_string3:
	.asciz	"run"                           # string offset=97
.Linfo_string4:
	.asciz	"main"                          # string offset=101
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
