	.file	"c.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c"
	.loc	1 24 0                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:24:0
	.cfi_startproc
# %bb.0:
	movl	$2, %eax
.Ltmp0:
	.loc	1 25 14 prologue_end            # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:25:14
	cmpl	$2, %edi
	je	.LBB0_1
# %bb.10:
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:30:1
	retq
.LBB0_1:
	.loc	1 0 1 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:0:1
	pushq	%r15
	.cfi_def_cfa_offset 16
	pushq	%r14
	.cfi_def_cfa_offset 24
	pushq	%r12
	.cfi_def_cfa_offset 32
	pushq	%rbx
	.cfi_def_cfa_offset 40
	pushq	%rax
	.cfi_def_cfa_offset 48
	.cfi_offset %rbx, -40
	.cfi_offset %r12, -32
	.cfi_offset %r14, -24
	.cfi_offset %r15, -16
	movabsq	$-3750763034362895579, %rbx     # imm = 0xCBF29CE484222325
	.loc	1 26 36 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:36
	movq	8(%rsi), %rdi
	.loc	1 26 27 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:27
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
.Ltmp1:
	.loc	1 21 28 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	testq	%rax, %rax
	.loc	1 21 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	je	.LBB0_9
# %bb.2:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:0:5
	movabsq	$1099511628211, %r9             # imm = 0x100000001B3
	movabsq	$5675921253449092805, %r10      # imm = 0x4EC4EC4EC4EC4EC5
	.loc	1 21 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	cmpq	$1, %rax
	jne	.LBB0_4
# %bb.3:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:0:5
	xorl	%ecx, %ecx
	.loc	1 21 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	jmp	.LBB0_8
.LBB0_4:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:0:5
	movq	%rax, %r11
	.loc	1 21 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	movq	%rax, %r14
	andq	$-2, %r14
	negq	%r14
	movl	$.L__const.run.pattern+1, %r15d
	movl	$1, %esi
	movl	$.L__const.run.pattern, %r12d
	xorl	%ecx, %ecx
	xorl	%edi, %edi
	.loc	1 0 5                           # :0:5
.Ltmp2:
	.p2align	4
.LBB0_5:                                # =>This Inner Loop Header: Depth=1
	.loc	1 21 62                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:62 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	movq	%rsi, %rax
	mulq	%r10
	movq	%rdx, %r8
	shrq	$2, %r8
	movq	%rdi, %rax
	mulq	%r10
	imulq	$-13, %r8, %rax
	shrq	$2, %rdx
	imulq	$-13, %rdx, %rdx
	.loc	1 21 52                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:52 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	movzbl	(%r12,%rdx), %edx
	.loc	1 21 49                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:49 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	xorq	%rbx, %rdx
	.loc	1 21 78                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:78 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	imulq	%r9, %rdx
	.loc	1 21 52                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:52 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	movzbl	(%r15,%rax), %ebx
	.loc	1 21 49                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:49 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	xorq	%rdx, %rbx
	.loc	1 21 78                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:78 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	imulq	%r9, %rbx
	.loc	1 21 33                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:33 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	addq	$2, %rdi
	.loc	1 21 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	addq	$2, %r15
	addq	$2, %rsi
	addq	$-2, %rcx
	addq	$2, %r12
	cmpq	%rcx, %r14
	jne	.LBB0_5
# %bb.6:
	testb	$1, %r11b
	je	.LBB0_9
# %bb.7:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:0:5
	negq	%rcx
.LBB0_8:
	.loc	1 21 62 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:62 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	movq	%rcx, %rax
	mulq	%r10
	shrq	$2, %rdx
	leaq	(%rdx,%rdx,2), %rax
	leaq	(%rdx,%rax,4), %rax
	.loc	1 21 52 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:52 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	negq	%rax
	movzbl	.L__const.run.pattern(%rcx,%rax), %eax
	.loc	1 21 49                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:49 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	xorq	%rbx, %rax
	.loc	1 21 78                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:21:78 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:26:23 ]
	imulq	%r9, %rax
	movq	%rax, %rbx
.Ltmp3:
.LBB0_9:
	.loc	1 27 13 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:27:13
	movq	stderr(%rip), %rdi
	.loc	1 27 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:27:5
	movl	$.L.str, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 28 5 is_stmt 1                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:28:5
	movl	$.L.str.1, %edi
	movq	%rbx, %rsi
	xorl	%eax, %eax
	callq	printf
	xorl	%eax, %eax
	addq	$8, %rsp
	.cfi_def_cfa_offset 40
	popq	%rbx
	.cfi_def_cfa_offset 32
	popq	%r12
	.cfi_def_cfa_offset 24
	popq	%r14
	.cfi_def_cfa_offset 16
	popq	%r15
	.cfi_def_cfa_offset 8
	.cfi_restore %rbx
	.cfi_restore %r12
	.cfi_restore %r14
	.cfi_restore %r15
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 30 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c:30:1
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

	.type	.L__const.run.pattern,@object   # @__const.run.pattern
.L__const.run.pattern:
	.asciz	"meldra-native"
	.size	.L__const.run.pattern, 14

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
	.byte	26                              # DW_AT_call_line
	.byte	23                              # DW_AT_call_column
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
.Ldebug_info_end0:
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/fnv_ascii/c.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=43
.Linfo_string3:
	.asciz	"run"                           # string offset=90
.Linfo_string4:
	.asciz	"main"                          # string offset=94
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
