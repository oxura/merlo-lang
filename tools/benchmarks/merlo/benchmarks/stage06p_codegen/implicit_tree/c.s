	.file	"c.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c"
	.loc	1 23 0                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:23:0
	.cfi_startproc
# %bb.0:
	movl	$2, %eax
.Ltmp0:
	.loc	1 24 14 prologue_end            # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:24:14
	cmpl	$2, %edi
	je	.LBB0_1
# %bb.9:
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 29 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:29:1
	retq
.LBB0_1:
	.loc	1 0 1 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:0:1
	pushq	%rbx
	.cfi_def_cfa_offset 16
	.cfi_offset %rbx, -16
	.loc	1 25 36 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:36
	movq	8(%rsi), %rdi
	.loc	1 25 27 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:27
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$0, %ebx
.Ltmp1:
	.loc	1 20 28 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	testq	%rax, %rax
	.loc	1 20 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	je	.LBB0_8
# %bb.2:
	cmpq	$1, %rax
	jne	.LBB0_4
# %bb.3:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:0:5
	xorl	%ebx, %ebx
	xorl	%ecx, %ecx
	.loc	1 20 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	jmp	.LBB0_7
.LBB0_4:
	movq	%rax, %rdx
	andq	$-2, %rdx
	movl	$4, %esi
	movl	$8, %edi
	xorl	%ebx, %ebx
	xorl	%ecx, %ecx
	.loc	1 0 5                           # :0:5
.Ltmp2:
	.p2align	4
.LBB0_5:                                # =>This Inner Loop Header: Depth=1
	.loc	1 20 136                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:136 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	leaq	-3(%rsi), %r8
	.loc	1 20 110                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:110 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	leaq	-4(%rdi), %r9
	imulq	%r8, %r9
	movq	%rdi, %r8
	imulq	%rsi, %r8
	.loc	1 20 96                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:96 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	xorq	%r8, %rbx
	xorq	%r9, %rbx
	.loc	1 20 33                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:33 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	addq	$2, %rcx
	.loc	1 20 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	addq	$6, %rsi
	addq	$8, %rdi
	cmpq	%rcx, %rdx
	jne	.LBB0_5
# %bb.6:
	testb	$1, %al
	je	.LBB0_8
.LBB0_7:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:0:5
	movq	%rbx, %rax
	.loc	1 20 59                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:59 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	leaq	(%rcx,%rcx,2), %rdx
	incq	%rdx
	.loc	1 20 136                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:136 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	leaq	4(,%rcx,4), %rbx
	.loc	1 20 110                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:110 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	imulq	%rdx, %rbx
	.loc	1 20 96                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:20:96 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:25:23 ]
	xorq	%rax, %rbx
.Ltmp3:
.LBB0_8:
	.loc	1 26 13 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:26:13
	movq	stderr(%rip), %rdi
	.loc	1 26 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:26:5
	movl	$.L.str, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 27 5 is_stmt 1                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:27:5
	movl	$.L.str.1, %edi
	movq	%rbx, %rsi
	xorl	%eax, %eax
	callq	printf
	xorl	%eax, %eax
	popq	%rbx
	.cfi_def_cfa_offset 8
	.cfi_restore %rbx
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 29 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c:29:1
	retq
.Ltmp4:
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
	.long	.Ltmp3-.Ltmp1                   # DW_AT_high_pc
	.byte	1                               # DW_AT_call_file
	.byte	25                              # DW_AT_call_line
	.byte	23                              # DW_AT_call_column
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
.Ldebug_info_end0:
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/implicit_tree/c.c" # string offset=1
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
