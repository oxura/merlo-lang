	.file	"c.c"
	.section	.rodata.cst16,"aM",@progbits,16
	.p2align	4, 0x0                          # -- Begin function main
.LCPI0_0:
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	1                               # 0x1
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
	.byte	0                               # 0x0
.LCPI0_1:
	.quad	2                               # 0x2
	.quad	2                               # 0x2
.LCPI0_2:
	.quad	4                               # 0x4
	.quad	4                               # 0x4
	.text
	.globl	main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c"
	.loc	1 23 0                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:23:0
	.cfi_startproc
# %bb.0:
	movl	$2, %eax
.Ltmp0:
	.loc	1 24 14 prologue_end            # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:24:14
	cmpl	$2, %edi
	je	.LBB0_1
# %bb.10:
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 29 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:29:1
	retq
.LBB0_1:
	.loc	1 0 1 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:0:1
	pushq	%rbx
	.cfi_def_cfa_offset 16
	.cfi_offset %rbx, -16
	.loc	1 25 36 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:36
	movq	8(%rsi), %rdi
	.loc	1 25 27 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:27
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$0, %ebx
.Ltmp1:
	.loc	1 20 28 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	testq	%rax, %rax
	.loc	1 20 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	je	.LBB0_9
# %bb.2:
	cmpq	$4, %rax
	jae	.LBB0_4
# %bb.3:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:0:5
	xorl	%ebx, %ebx
	xorl	%ecx, %ecx
	.loc	1 20 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	jmp	.LBB0_7
.LBB0_4:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:0:5
	movq	%rax, %rcx
	andq	$-4, %rcx
	movdqa	.LCPI0_0(%rip), %xmm1           # xmm1 = [0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0]
	pxor	%xmm0, %xmm0
	movdqa	.LCPI0_1(%rip), %xmm2           # xmm2 = [2,2]
	pcmpeqd	%xmm3, %xmm3
	movdqa	.LCPI0_2(%rip), %xmm5           # xmm5 = [4,4]
	movq	%rcx, %rdx
	pxor	%xmm4, %xmm4
	.p2align	4
.LBB0_5:                                # =>This Inner Loop Header: Depth=1
	movdqa	%xmm1, %xmm6
	paddq	%xmm2, %xmm6
	.loc	1 20 60 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:60 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	movdqa	%xmm1, %xmm7
	paddq	%xmm7, %xmm7
	paddq	%xmm1, %xmm7
	movdqa	%xmm6, %xmm8
	paddq	%xmm8, %xmm8
	paddq	%xmm6, %xmm8
	.loc	1 20 64 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:64 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	psubq	%xmm3, %xmm7
	psubq	%xmm3, %xmm8
	.loc	1 20 90                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:90 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	pxor	%xmm1, %xmm7
	.loc	1 20 79                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:79 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	paddq	%xmm7, %xmm4
	.loc	1 20 90                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:90 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	pxor	%xmm6, %xmm8
	.loc	1 20 79                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:79 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	paddq	%xmm8, %xmm0
	paddq	%xmm5, %xmm1
	.loc	1 20 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	addq	$-4, %rdx
	jne	.LBB0_5
# %bb.6:
	paddq	%xmm4, %xmm0
	pshufd	$238, %xmm0, %xmm1              # xmm1 = xmm0[2,3,2,3]
	paddq	%xmm0, %xmm1
	movq	%xmm1, %rbx
	cmpq	%rcx, %rax
	je	.LBB0_9
.LBB0_7:
	leaq	(%rcx,%rcx,2), %rdx
	incq	%rdx
	.loc	1 0 5                           # :0:5
.Ltmp2:
	.p2align	4
.LBB0_8:                                # =>This Inner Loop Header: Depth=1
	.loc	1 20 90                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:90 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	movq	%rdx, %rsi
	xorq	%rcx, %rsi
	.loc	1 20 79                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:79 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	addq	%rsi, %rbx
	.loc	1 20 33                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:33 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	incq	%rcx
	.loc	1 20 28                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	addq	$3, %rdx
	cmpq	%rcx, %rax
	.loc	1 20 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:20:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:25:23 ]
	jne	.LBB0_8
.Ltmp3:
.LBB0_9:
	.loc	1 26 13 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:26:13
	movq	stderr(%rip), %rdi
	.loc	1 26 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:26:5
	movl	$.L.str, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 27 5 is_stmt 1                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:27:5
	movl	$.L.str.1, %edi
	movq	%rbx, %rsi
	xorl	%eax, %eax
	callq	printf
	xorl	%eax, %eax
	popq	%rbx
	.cfi_def_cfa_offset 8
	.cfi_restore %rbx
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 29 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c:29:1
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
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/record_values/c.c" # string offset=1
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
