	.file	"meldra.c"
	.section	.rodata.cst16,"aM",@progbits,16
	.p2align	4, 0x0                          # -- Begin function main
.LCPI0_0:
	.quad	3                               # 0x3
	.quad	4                               # 0x4
.LCPI0_1:
	.quad	5                               # 0x5
	.quad	6                               # 0x6
.LCPI0_2:
	.quad	7                               # 0x7
	.quad	8                               # 0x8
	.text
	.globl	main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c"
	.loc	1 134 0                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:134:0
	.cfi_startproc
# %bb.0:
	pushq	%r14
	.cfi_def_cfa_offset 16
	pushq	%rbx
	.cfi_def_cfa_offset 24
	subq	$72, %rsp
	.cfi_def_cfa_offset 96
	.cfi_offset %rbx, -24
	.cfi_offset %r14, -16
	.loc	1 135 14 prologue_end           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:135:14
	cmpl	$2, %edi
	jne	.LBB0_1
# %bb.2:
	.loc	1 136 59                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:136:59
	movq	8(%rsi), %rdi
	xorl	%ebx, %ebx
	.loc	1 136 50 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:136:50
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
.Ltmp0:
	.loc	1 65 25 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:65:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movq	$3, (%rsp)
	.loc	1 66 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:66:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movq	$1, 8(%rsp)
	.loc	1 67 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:67:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movq	$4, 16(%rsp)
	.loc	1 68 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:68:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movq	$1, 24(%rsp)
	.loc	1 69 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:69:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movq	$5, 32(%rsp)
	.loc	1 70 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:70:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movq	$9, 40(%rsp)
	.loc	1 71 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:71:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movq	$2, 48(%rsp)
	.loc	1 72 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:72:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movq	$6, 56(%rsp)
	movl	$0, %r14d
	.loc	1 90 36                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:90:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	testq	%rax, %rax
	.loc	1 91 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:91:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	je	.LBB0_14
# %bb.3:
	leaq	-9(%rax), %rcx
	cmpq	$-5, %rcx
	jae	.LBB0_5
# %bb.4:
	.loc	1 0 9 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:0:9
	xorl	%r14d, %r14d
	xorl	%ecx, %ecx
	.loc	1 91 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:91:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	jmp	.LBB0_8
.Ltmp1:
.LBB0_1:
	.loc	1 135 62 is_stmt 1              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:135:62
	movq	stderr(%rip), %rcx
	.loc	1 135 22 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:135:22
	movl	$.L.str, %edi
	movl	$29, %esi
	movl	$1, %edx
	callq	fwrite@PLT
	movl	$2, %ebx
	.loc	1 135 71                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:135:71
	jmp	.LBB0_15
.LBB0_5:
	.loc	1 0 71                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:0:71
	movl	%eax, %ecx
	andl	$12, %ecx
.Ltmp2:
	.loc	1 106 28 is_stmt 1              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:106:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movdqa	(%rsp), %xmm2
	movdqa	16(%rsp), %xmm0
	.loc	1 114 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:114:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movdqa	%xmm2, %xmm1
	paddq	%xmm1, %xmm1
	movsd	%xmm2, %xmm1                    # xmm1 = xmm2[0],xmm1[1]
	movdqa	.LCPI0_0(%rip), %xmm2           # xmm2 = [3,4]
	movdqa	%xmm0, %xmm3
	pmuludq	%xmm2, %xmm3
	psrlq	$32, %xmm0
	pmuludq	%xmm2, %xmm0
	psllq	$32, %xmm0
	paddq	%xmm3, %xmm0
	.loc	1 91 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:91:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	cmpq	$4, %rcx
	je	.LBB0_7
# %bb.6:
	.loc	1 106 28                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:106:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movdqa	32(%rsp), %xmm2
	movdqa	48(%rsp), %xmm3
	.loc	1 114 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:114:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movdqa	.LCPI0_1(%rip), %xmm4           # xmm4 = [5,6]
	movdqa	%xmm2, %xmm5
	pmuludq	%xmm4, %xmm5
	psrlq	$32, %xmm2
	pmuludq	%xmm4, %xmm2
	psllq	$32, %xmm2
	movdqa	.LCPI0_2(%rip), %xmm4           # xmm4 = [7,8]
	movdqa	%xmm3, %xmm6
	pmuludq	%xmm4, %xmm6
	psrlq	$32, %xmm3
	pmuludq	%xmm4, %xmm3
	psllq	$32, %xmm3
	paddq	%xmm6, %xmm3
	paddq	%xmm1, %xmm5
	.loc	1 116 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:116:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	paddq	%xmm2, %xmm5
	paddq	%xmm3, %xmm0
	movdqa	%xmm5, %xmm1
.LBB0_7:
	.loc	1 91 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:91:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	paddq	%xmm0, %xmm1
	pshufd	$238, %xmm1, %xmm0              # xmm0 = xmm1[2,3,2,3]
	paddq	%xmm1, %xmm0
	movq	%xmm0, %r14
	cmpq	%rcx, %rax
	je	.LBB0_14
.LBB0_8:
	movq	%rax, %rsi
	andq	$3, %rsi
	jne	.LBB0_10
# %bb.9:
	.loc	1 0 9 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:0:9
	movq	%rcx, %rdx
	.loc	1 91 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:91:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	jmp	.LBB0_12
.LBB0_10:
	movl	%ecx, %edx
	andl	$7, %edx
	leaq	(%rsp,%rdx,8), %rdi
	xorl	%r8d, %r8d
	movq	%rcx, %rdx
	.loc	1 0 9                           # :0:9
.Ltmp3:
	.p2align	4
.LBB0_11:                               # =>This Inner Loop Header: Depth=1
	.loc	1 112 40 is_stmt 1              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:112:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	incq	%rdx
	movq	(%rdi,%r8,8), %r9
	.loc	1 114 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:114:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	imulq	%rdx, %r9
	.loc	1 116 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:116:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	addq	%r9, %r14
	.loc	1 91 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:91:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	incq	%r8
	cmpq	%r8, %rsi
	jne	.LBB0_11
.LBB0_12:
	subq	%rax, %rcx
	cmpq	$-4, %rcx
	ja	.LBB0_14
	.loc	1 0 9 is_stmt 0                 # :0:9
.Ltmp4:
	.p2align	4
.LBB0_13:                               # =>This Inner Loop Header: Depth=1
	.loc	1 100 40 is_stmt 1              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:100:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	movl	%edx, %ecx
	andl	$7, %ecx
	.loc	1 114 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:114:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	leaq	1(%rdx), %rsi
	movq	(%rsp,%rcx,8), %rcx
	imulq	%rsi, %rcx
	.loc	1 116 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:116:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	addq	%r14, %rcx
	.loc	1 100 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:100:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	andl	$7, %esi
	.loc	1 114 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:114:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	leaq	2(%rdx), %rdi
	movq	(%rsp,%rsi,8), %rsi
	imulq	%rdi, %rsi
	.loc	1 116 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:116:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	addq	%rcx, %rsi
	.loc	1 100 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:100:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	andl	$7, %edi
	.loc	1 114 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:114:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	leaq	3(%rdx), %rcx
	movq	(%rsp,%rdi,8), %rdi
	imulq	%rcx, %rdi
	.loc	1 100 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:100:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	andl	$7, %ecx
	.loc	1 112 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:112:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	addq	$4, %rdx
	movq	(%rsp,%rcx,8), %r14
	.loc	1 114 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:114:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	imulq	%rdx, %r14
	.loc	1 116 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:116:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	addq	%rdi, %r14
	addq	%rsi, %r14
	.loc	1 90 36                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:90:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	cmpq	%rdx, %rax
	.loc	1 91 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:91:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:137:23 ]
	jne	.LBB0_13
.Ltmp5:
.LBB0_14:
	.loc	1 138 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:138:13
	movq	stderr(%rip), %rdi
	.loc	1 138 5 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:138:5
	movl	$.L.str.1, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 139 5 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:139:5
	movl	$.L.str.2, %edi
	movq	%r14, %rsi
	xorl	%eax, %eax
	callq	printf
.LBB0_15:
	.loc	1 141 1                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:141:1
	movl	%ebx, %eax
	.loc	1 141 1 epilogue_begin is_stmt 0 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c:141:1
	addq	$72, %rsp
	.cfi_def_cfa_offset 24
	popq	%rbx
	.cfi_def_cfa_offset 16
	popq	%r14
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
	.byte	137                             # DW_AT_call_line
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
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/fixed_array_scan/meldra.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=55
.Linfo_string3:
	.asciz	"meldra_fn_main"                # string offset=102
.Linfo_string4:
	.asciz	"main"                          # string offset=117
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
