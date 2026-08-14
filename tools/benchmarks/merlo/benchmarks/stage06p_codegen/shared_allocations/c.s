	.file	"c.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c"
	.loc	1 23 0                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:23:0
	.cfi_startproc
# %bb.0:
	movl	$2, %eax
.Ltmp0:
	.loc	1 24 14 prologue_end            # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:24:14
	cmpl	$2, %edi
	je	.LBB0_1
# %bb.5:
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 29 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:29:1
	retq
.LBB0_1:
	.loc	1 0 1 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:0:1
	pushq	%r15
	.cfi_def_cfa_offset 16
	pushq	%r14
	.cfi_def_cfa_offset 24
	pushq	%rbx
	.cfi_def_cfa_offset 32
	.cfi_offset %rbx, -32
	.cfi_offset %r14, -24
	.cfi_offset %r15, -16
	.loc	1 25 36 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:36
	movq	8(%rsi), %rdi
	.loc	1 25 27 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:27
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$0, %ebx
.Ltmp1:
	.loc	1 20 28 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:20:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:23 ]
	testq	%rax, %rax
	.loc	1 20 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:20:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:23 ]
	je	.LBB0_4
# %bb.2:
	.loc	1 0 5                           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:0:5
	movq	%rax, %r14
	xorl	%ebx, %ebx
	xorl	%r15d, %r15d
	.p2align	4
.LBB0_3:                                # =>This Inner Loop Header: Depth=1
	.loc	1 20 59 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:20:59 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:23 ]
	movq	%r15, %rdi
	callq	make_values
	.loc	1 20 97 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:20:97 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:23 ]
	addq	(%rax), %rbx
	.loc	1 20 84                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:20:84 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:23 ]
	addq	56(%rax), %rbx
	.loc	1 20 110                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:20:110 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:23 ]
	movq	%rax, %rdi
	callq	free
	.loc	1 20 33                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:20:33 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:23 ]
	incq	%r15
	.loc	1 20 28                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:20:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:23 ]
	cmpq	%r15, %r14
	.loc	1 20 5                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:20:5 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:25:23 ]
	jne	.LBB0_3
.Ltmp2:
.LBB0_4:
	.loc	1 26 13 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:26:13
	movq	stderr(%rip), %rdi
	.loc	1 26 5 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:26:5
	movl	$.L.str, %esi
	movl	$500000, %edx                   # imm = 0x7A120
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 27 5 is_stmt 1                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:27:5
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
	.loc	1 29 1                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:29:1
	retq
.Ltmp3:
.Lfunc_end0:
	.size	main, .Lfunc_end0-main
	.cfi_endproc
                                        # -- End function
	.section	.rodata.cst16,"aM",@progbits,16
	.p2align	4, 0x0                          # -- Begin function make_values
.LCPI1_0:
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
.LCPI1_1:
	.quad	2                               # 0x2
	.quad	3                               # 0x3
.LCPI1_2:
	.quad	4                               # 0x4
	.quad	5                               # 0x5
.LCPI1_3:
	.quad	6                               # 0x6
	.quad	7                               # 0x7
	.text
	.p2align	4
	.type	make_values,@function
make_values:                            # @make_values
.Lfunc_begin1:
	.loc	1 10 0                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:10:0
	.cfi_startproc
# %bb.0:
	pushq	%rbx
	.cfi_def_cfa_offset 16
	.cfi_offset %rbx, -16
	movq	%rdi, %rbx
.Ltmp4:
	.loc	1 11 24 prologue_end            # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:11:24
	movl	$64, %edi
	callq	malloc
	.loc	1 12 10                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:12:10
	testq	%rax, %rax
	.loc	1 12 9 is_stmt 0                # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:12:9
	je	.LBB1_2
# %bb.1:
	.loc	1 13 52 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:13:52
	movq	%rbx, %xmm0
	pshufd	$68, %xmm0, %xmm0               # xmm0 = xmm0[0,1,0,1]
	movdqa	.LCPI1_0(%rip), %xmm1           # xmm1 = [0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0]
	paddq	%xmm0, %xmm1
	movdqa	.LCPI1_1(%rip), %xmm2           # xmm2 = [2,3]
	paddq	%xmm0, %xmm2
	.loc	1 13 48 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:13:48
	movdqu	%xmm2, 16(%rax)
	movdqa	.LCPI1_2(%rip), %xmm2           # xmm2 = [4,5]
	.loc	1 13 52                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:13:52
	paddq	%xmm0, %xmm2
	paddq	.LCPI1_3(%rip), %xmm0
	.loc	1 13 48                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:13:48
	movdqu	%xmm1, (%rax)
	movdqu	%xmm0, 48(%rax)
	movdqu	%xmm2, 32(%rax)
	.loc	1 14 5 epilogue_begin is_stmt 1 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:14:5
	popq	%rbx
	.cfi_def_cfa_offset 8
	retq
.LBB1_2:
	.cfi_def_cfa_offset 16
	.loc	1 12 18                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c:12:18
	callq	abort
.Ltmp5:
.Lfunc_end1:
	.size	make_values, .Lfunc_end1-make_values
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
	.long	.Lfunc_end1-.Lfunc_begin0       # DW_AT_high_pc
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
	.byte	25                              # DW_AT_call_line
	.byte	23                              # DW_AT_call_column
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
.Ldebug_info_end0:
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/c.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=52
.Linfo_string3:
	.asciz	"run"                           # string offset=99
.Linfo_string4:
	.asciz	"main"                          # string offset=103
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
