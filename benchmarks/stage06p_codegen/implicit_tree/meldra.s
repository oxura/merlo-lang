	.file	"meldra.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "benchmarks/stage06p_codegen/implicit_tree/meldra.c"
	.loc	1 135 0                         # benchmarks/stage06p_codegen/implicit_tree/meldra.c:135:0
	.cfi_startproc
# %bb.0:
	pushq	%r14
	.cfi_def_cfa_offset 16
	pushq	%rbx
	.cfi_def_cfa_offset 24
	pushq	%rax
	.cfi_def_cfa_offset 32
	.cfi_offset %rbx, -24
	.cfi_offset %r14, -16
	.loc	1 136 14 prologue_end           # benchmarks/stage06p_codegen/implicit_tree/meldra.c:136:14
	cmpl	$2, %edi
	jne	.LBB0_1
# %bb.2:
	.loc	1 137 59                        # benchmarks/stage06p_codegen/implicit_tree/meldra.c:137:59
	movq	8(%rsi), %rdi
	xorl	%ebx, %ebx
	.loc	1 137 50 is_stmt 0              # benchmarks/stage06p_codegen/implicit_tree/meldra.c:137:50
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$0, %r14d
.Ltmp0:
	.loc	1 65 34 is_stmt 1               # benchmarks/stage06p_codegen/implicit_tree/meldra.c:65:34 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	testq	%rax, %rax
	.loc	1 66 9                          # benchmarks/stage06p_codegen/implicit_tree/meldra.c:66:9 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	je	.LBB0_9
# %bb.3:
	cmpq	$1, %rax
	jne	.LBB0_5
# %bb.4:
	.loc	1 0 9 is_stmt 0                 # benchmarks/stage06p_codegen/implicit_tree/meldra.c:0:9
	xorl	%r14d, %r14d
	xorl	%ecx, %ecx
	.loc	1 66 9                          # benchmarks/stage06p_codegen/implicit_tree/meldra.c:66:9 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	jmp	.LBB0_8
.Ltmp1:
.LBB0_1:
	.loc	1 136 62 is_stmt 1              # benchmarks/stage06p_codegen/implicit_tree/meldra.c:136:62
	movq	stderr(%rip), %rcx
	.loc	1 136 22 is_stmt 0              # benchmarks/stage06p_codegen/implicit_tree/meldra.c:136:22
	movl	$.L.str, %edi
	movl	$29, %esi
	movl	$1, %edx
	callq	fwrite@PLT
	movl	$2, %ebx
	.loc	1 136 71                        # benchmarks/stage06p_codegen/implicit_tree/meldra.c:136:71
	jmp	.LBB0_10
.LBB0_5:
.Ltmp2:
	.loc	1 66 9 is_stmt 1                # benchmarks/stage06p_codegen/implicit_tree/meldra.c:66:9 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	movq	%rax, %rdx
	andq	$-2, %rdx
	movl	$4, %esi
	movl	$8, %edi
	xorl	%r14d, %r14d
	xorl	%ecx, %ecx
	.loc	1 0 9 is_stmt 0                 # :0:9
.Ltmp3:
	.p2align	4
.LBB0_6:                                # =>This Inner Loop Header: Depth=1
	.loc	1 113 40 is_stmt 1              # benchmarks/stage06p_codegen/implicit_tree/meldra.c:113:40 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	leaq	-3(%rsi), %r8
	.loc	1 115 40                        # benchmarks/stage06p_codegen/implicit_tree/meldra.c:115:40 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	leaq	-4(%rdi), %r9
	imulq	%r8, %r9
	movq	%rdi, %r8
	imulq	%rsi, %r8
	.loc	1 117 40                        # benchmarks/stage06p_codegen/implicit_tree/meldra.c:117:40 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	xorq	%r8, %r14
	xorq	%r9, %r14
	.loc	1 125 40                        # benchmarks/stage06p_codegen/implicit_tree/meldra.c:125:40 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	addq	$2, %rcx
	.loc	1 66 9                          # benchmarks/stage06p_codegen/implicit_tree/meldra.c:66:9 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	addq	$6, %rsi
	addq	$8, %rdi
	cmpq	%rcx, %rdx
	jne	.LBB0_6
# %bb.7:
	testb	$1, %al
	je	.LBB0_9
.LBB0_8:
	.loc	1 0 9 is_stmt 0                 # benchmarks/stage06p_codegen/implicit_tree/meldra.c:0:9
	movq	%r14, %rax
	.loc	1 77 40 is_stmt 1               # benchmarks/stage06p_codegen/implicit_tree/meldra.c:77:40 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	leaq	(%rcx,%rcx,2), %rdx
	incq	%rdx
	.loc	1 113 40                        # benchmarks/stage06p_codegen/implicit_tree/meldra.c:113:40 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	leaq	4(,%rcx,4), %r14
	.loc	1 115 40                        # benchmarks/stage06p_codegen/implicit_tree/meldra.c:115:40 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	imulq	%rdx, %r14
	.loc	1 117 40                        # benchmarks/stage06p_codegen/implicit_tree/meldra.c:117:40 @[ benchmarks/stage06p_codegen/implicit_tree/meldra.c:138:23 ]
	xorq	%rax, %r14
.Ltmp4:
.LBB0_9:
	.loc	1 139 13                        # benchmarks/stage06p_codegen/implicit_tree/meldra.c:139:13
	movq	stderr(%rip), %rdi
	.loc	1 139 5 is_stmt 0               # benchmarks/stage06p_codegen/implicit_tree/meldra.c:139:5
	movl	$.L.str.1, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 140 5 is_stmt 1               # benchmarks/stage06p_codegen/implicit_tree/meldra.c:140:5
	movl	$.L.str.2, %edi
	movq	%r14, %rsi
	xorl	%eax, %eax
	callq	printf
.LBB0_10:
	.loc	1 142 1                         # benchmarks/stage06p_codegen/implicit_tree/meldra.c:142:1
	movl	%ebx, %eax
	.loc	1 142 1 epilogue_begin is_stmt 0 # benchmarks/stage06p_codegen/implicit_tree/meldra.c:142:1
	addq	$8, %rsp
	.cfi_def_cfa_offset 24
	popq	%rbx
	.cfi_def_cfa_offset 16
	popq	%r14
	.cfi_def_cfa_offset 8
	retq
.Ltmp5:
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
	.byte	138                             # DW_AT_call_line
	.byte	23                              # DW_AT_call_column
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
.Ldebug_info_end0:
	.section	.debug_ranges,"",@progbits
.Ldebug_ranges0:
	.quad	.Ltmp0-.Lfunc_begin0
	.quad	.Ltmp1-.Lfunc_begin0
	.quad	.Ltmp2-.Lfunc_begin0
	.quad	.Ltmp4-.Lfunc_begin0
	.quad	0
	.quad	0
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"benchmarks/stage06p_codegen/implicit_tree/meldra.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=52
.Linfo_string3:
	.asciz	"meldra_fn_main"                # string offset=99
.Linfo_string4:
	.asciz	"main"                          # string offset=114
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
