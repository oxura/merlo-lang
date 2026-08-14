	.file	"meldra.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c"
	.loc	1 205 0                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:205:0
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
	.loc	1 206 14 prologue_end           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:206:14
	cmpl	$2, %edi
	jne	.LBB0_1
# %bb.2:
	.loc	1 207 59                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:207:59
	movq	8(%rsi), %rdi
	xorl	%ebx, %ebx
	.loc	1 207 50 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:207:50
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$0, %r14d
.Ltmp0:
	.loc	1 61 34 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:61:34 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:208:23 ]
	testq	%rax, %rax
	.loc	1 62 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:62:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:208:23 ]
	je	.LBB0_4
# %bb.3:
	leaq	(%rax,%rax,8), %rcx
	leaq	-1(%rax), %rdx
	addq	$-2, %rax
	imulq	%rdx, %rax
	andq	$-2, %rax
	leaq	(%rax,%rcx), %r14
	addq	$-2, %r14
.Ltmp1:
.LBB0_4:
	.loc	1 209 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:209:13
	movq	stderr(%rip), %rdi
	.loc	1 209 5 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:209:5
	movl	$.L.str.1, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 210 5 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:210:5
	movl	$.L.str.2, %edi
	movq	%r14, %rsi
	xorl	%eax, %eax
	callq	printf
	jmp	.LBB0_5
.LBB0_1:
	.loc	1 206 62                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:206:62
	movq	stderr(%rip), %rcx
	.loc	1 206 22 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:206:22
	movl	$.L.str, %edi
	movl	$29, %esi
	movl	$1, %edx
	callq	fwrite@PLT
	movl	$2, %ebx
.LBB0_5:
	.loc	1 212 1 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:212:1
	movl	%ebx, %eax
	.loc	1 212 1 epilogue_begin is_stmt 0 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c:212:1
	addq	$8, %rsp
	.cfi_def_cfa_offset 24
	popq	%rbx
	.cfi_def_cfa_offset 16
	popq	%r14
	.cfi_def_cfa_offset 8
	retq
.Ltmp2:
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
	.quad	.Ltmp0                          # DW_AT_low_pc
	.long	.Ltmp1-.Ltmp0                   # DW_AT_high_pc
	.byte	1                               # DW_AT_call_file
	.byte	208                             # DW_AT_call_line
	.byte	23                              # DW_AT_call_column
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
.Ldebug_info_end0:
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/shared_allocations/meldra.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=57
.Linfo_string3:
	.asciz	"meldra_fn_main"                # string offset=104
.Linfo_string4:
	.asciz	"main"                          # string offset=119
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
