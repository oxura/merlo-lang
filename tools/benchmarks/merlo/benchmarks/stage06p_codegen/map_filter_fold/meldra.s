	.file	"meldra.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c"
	.loc	1 180 0                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:180:0
	.cfi_startproc
# %bb.0:
	pushq	%r15
	.cfi_def_cfa_offset 16
	pushq	%r14
	.cfi_def_cfa_offset 24
	pushq	%r12
	.cfi_def_cfa_offset 32
	pushq	%rbx
	.cfi_def_cfa_offset 40
	subq	$72, %rsp
	.cfi_def_cfa_offset 112
	.cfi_offset %rbx, -40
	.cfi_offset %r12, -32
	.cfi_offset %r14, -24
	.cfi_offset %r15, -16
	.loc	1 181 14 prologue_end           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:181:14
	cmpl	$2, %edi
	jne	.LBB0_1
# %bb.2:
	.loc	1 182 59                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:182:59
	movq	8(%rsi), %rdi
	xorl	%ebx, %ebx
	.loc	1 182 50 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:182:50
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
.Ltmp0:
	.loc	1 88 25 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:88:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	$1, (%rsp)
	.loc	1 89 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:89:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	$2, 8(%rsp)
	.loc	1 90 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:90:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	$3, 16(%rsp)
	.loc	1 91 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:91:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	$4, 24(%rsp)
	.loc	1 92 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:92:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	$5, 32(%rsp)
	.loc	1 93 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:93:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	$6, 40(%rsp)
	.loc	1 94 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:94:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	$7, 48(%rsp)
	.loc	1 95 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:95:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	$8, 56(%rsp)
	movl	$0, %r14d
	.loc	1 113 36                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:113:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	testq	%rax, %rax
	.loc	1 114 9                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:114:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	je	.LBB0_5
# %bb.3:
	.loc	1 0 9 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:0:9
	xorl	%ecx, %ecx
	xorl	%esi, %esi
	xorl	%edx, %edx
	.p2align	4
.LBB0_4:                                # =>This Inner Loop Header: Depth=1
	.loc	1 121 40 is_stmt 1              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:121:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movl	%edx, %edi
	andl	$7, %edi
	.loc	1 143 35                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:143:35 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	addq	%rdx, (%rsp,%rdi,8)
	.loc	1 151 55                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:55 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	(%rsp), %rdi
	movq	8(%rsp), %r8
.Ltmp1:
	.loc	1 176 42                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:176:42 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:38 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	imulq	%rdi, %rdi
.Ltmp2:
	.loc	1 60 34                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:60:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	testb	$1, %dil
.Ltmp3:
	.loc	1 152 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	cmovneq	%rcx, %rdi
.Ltmp4:
	.loc	1 176 42                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:176:42 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:38 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	imulq	%r8, %r8
.Ltmp5:
	.loc	1 60 34                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:60:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	testb	$1, %r8b
.Ltmp6:
	.loc	1 152 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	cmovneq	%rcx, %r8
	.loc	1 151 55                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:55 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	16(%rsp), %r9
.Ltmp7:
	.loc	1 176 42                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:176:42 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:38 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	imulq	%r9, %r9
.Ltmp8:
	.loc	1 60 34                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:60:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	testb	$1, %r9b
.Ltmp9:
	.loc	1 152 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	cmovneq	%rcx, %r9
	.loc	1 151 55                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:55 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	24(%rsp), %r11
.Ltmp10:
	.loc	1 176 42                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:176:42 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:38 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	imulq	%r11, %r11
.Ltmp11:
	.loc	1 60 34                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:60:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	testb	$1, %r11b
.Ltmp12:
	.loc	1 152 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	cmovneq	%rcx, %r11
	.loc	1 151 55                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:55 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	32(%rsp), %r10
.Ltmp13:
	.loc	1 176 42                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:176:42 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:38 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	imulq	%r10, %r10
.Ltmp14:
	.loc	1 60 34                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:60:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	testb	$1, %r10b
.Ltmp15:
	.loc	1 152 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	cmovneq	%rcx, %r10
	.loc	1 151 55                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:55 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	40(%rsp), %r15
.Ltmp16:
	.loc	1 176 42                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:176:42 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:38 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	imulq	%r15, %r15
.Ltmp17:
	.loc	1 60 34                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:60:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	testb	$1, %r15b
.Ltmp18:
	.loc	1 152 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	cmovneq	%rcx, %r15
	.loc	1 151 55                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:55 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	48(%rsp), %r12
.Ltmp19:
	.loc	1 176 42                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:176:42 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:38 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	imulq	%r12, %r12
.Ltmp20:
	.loc	1 60 34                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:60:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	testb	$1, %r12b
.Ltmp21:
	.loc	1 152 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	cmovneq	%rcx, %r12
	.loc	1 151 55                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:55 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	movq	56(%rsp), %r14
.Ltmp22:
	.loc	1 176 42                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:176:42 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:151:38 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	imulq	%r14, %r14
.Ltmp23:
	.loc	1 60 34                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:60:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ] ]
	testb	$1, %r14b
.Ltmp24:
	.loc	1 152 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:152:13 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	cmovneq	%rcx, %r14
	addq	%rsi, %rdi
	addq	%r8, %r9
	addq	%rdi, %r9
	addq	%r11, %r10
	addq	%r15, %r10
	addq	%r9, %r10
	addq	%r12, %r14
	.loc	1 155 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:155:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	addq	%r10, %r14
	.loc	1 163 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:163:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	incq	%rdx
	movq	%r14, %rsi
	.loc	1 113 36                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:113:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	cmpq	%rdx, %rax
	.loc	1 114 9                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:114:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:183:23 ]
	jne	.LBB0_4
.Ltmp25:
.LBB0_5:
	.loc	1 184 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:184:13
	movq	stderr(%rip), %rdi
	.loc	1 184 5 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:184:5
	movl	$.L.str.1, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 185 5 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:185:5
	movl	$.L.str.2, %edi
	movq	%r14, %rsi
	xorl	%eax, %eax
	callq	printf
	jmp	.LBB0_6
.LBB0_1:
	.loc	1 181 62                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:181:62
	movq	stderr(%rip), %rcx
	.loc	1 181 22 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:181:22
	movl	$.L.str, %edi
	movl	$29, %esi
	movl	$1, %edx
	callq	fwrite@PLT
	movl	$2, %ebx
.LBB0_6:
	.loc	1 187 1 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:187:1
	movl	%ebx, %eax
	.loc	1 187 1 epilogue_begin is_stmt 0 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c:187:1
	addq	$72, %rsp
	.cfi_def_cfa_offset 40
	popq	%rbx
	.cfi_def_cfa_offset 32
	popq	%r12
	.cfi_def_cfa_offset 24
	popq	%r14
	.cfi_def_cfa_offset 16
	popq	%r15
	.cfi_def_cfa_offset 8
	retq
.Ltmp26:
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
	.byte	1                               # DW_CHILDREN_yes
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
	.byte	5                               # Abbreviation Code
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
	.byte	1                               # Abbrev [1] 0xb:0x71 DW_TAG_compile_unit
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
	.byte	2                               # Abbrev [2] 0x30:0x6 DW_TAG_subprogram
	.long	.Linfo_string4                  # DW_AT_name
	.byte	1                               # DW_AT_inline
	.byte	2                               # Abbrev [2] 0x36:0x6 DW_TAG_subprogram
	.long	.Linfo_string5                  # DW_AT_name
	.byte	1                               # DW_AT_inline
	.byte	3                               # Abbrev [3] 0x3c:0x3f DW_TAG_subprogram
	.quad	.Lfunc_begin0                   # DW_AT_low_pc
	.long	.Lfunc_end0-.Lfunc_begin0       # DW_AT_high_pc
	.long	.Linfo_string6                  # DW_AT_name
	.byte	4                               # Abbrev [4] 0x4d:0x2d DW_TAG_inlined_subroutine
	.long	42                              # DW_AT_abstract_origin
	.quad	.Ltmp0                          # DW_AT_low_pc
	.long	.Ltmp25-.Ltmp0                  # DW_AT_high_pc
	.byte	1                               # DW_AT_call_file
	.byte	183                             # DW_AT_call_line
	.byte	23                              # DW_AT_call_column
	.byte	5                               # Abbrev [5] 0x61:0xc DW_TAG_inlined_subroutine
	.long	48                              # DW_AT_abstract_origin
	.long	.Ldebug_ranges0                 # DW_AT_ranges
	.byte	1                               # DW_AT_call_file
	.byte	151                             # DW_AT_call_line
	.byte	38                              # DW_AT_call_column
	.byte	5                               # Abbrev [5] 0x6d:0xc DW_TAG_inlined_subroutine
	.long	54                              # DW_AT_abstract_origin
	.long	.Ldebug_ranges1                 # DW_AT_ranges
	.byte	1                               # DW_AT_call_file
	.byte	152                             # DW_AT_call_line
	.byte	13                              # DW_AT_call_column
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
.Ldebug_info_end0:
	.section	.debug_ranges,"",@progbits
.Ldebug_ranges0:
	.quad	.Ltmp1-.Lfunc_begin0
	.quad	.Ltmp2-.Lfunc_begin0
	.quad	.Ltmp4-.Lfunc_begin0
	.quad	.Ltmp5-.Lfunc_begin0
	.quad	.Ltmp7-.Lfunc_begin0
	.quad	.Ltmp8-.Lfunc_begin0
	.quad	.Ltmp10-.Lfunc_begin0
	.quad	.Ltmp11-.Lfunc_begin0
	.quad	.Ltmp13-.Lfunc_begin0
	.quad	.Ltmp14-.Lfunc_begin0
	.quad	.Ltmp16-.Lfunc_begin0
	.quad	.Ltmp17-.Lfunc_begin0
	.quad	.Ltmp19-.Lfunc_begin0
	.quad	.Ltmp20-.Lfunc_begin0
	.quad	.Ltmp22-.Lfunc_begin0
	.quad	.Ltmp23-.Lfunc_begin0
	.quad	0
	.quad	0
.Ldebug_ranges1:
	.quad	.Ltmp2-.Lfunc_begin0
	.quad	.Ltmp3-.Lfunc_begin0
	.quad	.Ltmp5-.Lfunc_begin0
	.quad	.Ltmp6-.Lfunc_begin0
	.quad	.Ltmp8-.Lfunc_begin0
	.quad	.Ltmp9-.Lfunc_begin0
	.quad	.Ltmp11-.Lfunc_begin0
	.quad	.Ltmp12-.Lfunc_begin0
	.quad	.Ltmp14-.Lfunc_begin0
	.quad	.Ltmp15-.Lfunc_begin0
	.quad	.Ltmp17-.Lfunc_begin0
	.quad	.Ltmp18-.Lfunc_begin0
	.quad	.Ltmp20-.Lfunc_begin0
	.quad	.Ltmp21-.Lfunc_begin0
	.quad	.Ltmp23-.Lfunc_begin0
	.quad	.Ltmp24-.Lfunc_begin0
	.quad	0
	.quad	0
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/map_filter_fold/meldra.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=54
.Linfo_string3:
	.asciz	"meldra_fn_main"                # string offset=101
.Linfo_string4:
	.asciz	"meldra_fn_square"              # string offset=116
.Linfo_string5:
	.asciz	"meldra_fn_even"                # string offset=133
.Linfo_string6:
	.asciz	"main"                          # string offset=148
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
