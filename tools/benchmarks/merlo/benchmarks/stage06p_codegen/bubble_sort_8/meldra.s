	.file	"meldra.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c"
	.loc	1 246 0                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:246:0
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
	pushq	%rax
	.cfi_def_cfa_offset 48
	.cfi_offset %rbx, -40
	.cfi_offset %r12, -32
	.cfi_offset %r14, -24
	.cfi_offset %r15, -16
	.loc	1 247 14 prologue_end           # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:247:14
	cmpl	$2, %edi
	jne	.LBB0_1
# %bb.2:
	.loc	1 248 59                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:248:59
	movq	8(%rsi), %rdi
	xorl	%ebx, %ebx
	.loc	1 248 50 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:248:50
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	movl	$1183, %r14d                    # imm = 0x49F
.Ltmp0:
	.loc	1 84 36 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:84:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	testq	%rax, %rax
	.loc	1 85 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:85:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	je	.LBB0_6
# %bb.3:
	.loc	1 0 9 is_stmt 0                 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:0:9
	movl	$9, %r14d
	movl	$1, %r9d
	movl	$8, %r8d
	movl	$2, %r10d
	movl	$7, %edi
	movl	$3, %esi
	movl	$6, %edx
	movl	$4, %ecx
	.p2align	4
.LBB0_4:                                # =>This Inner Loop Header: Depth=1
	.loc	1 179 9 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:179:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	cmpq	%r9, %r14
	movq	%r9, %r11
	cmovaq	%r14, %r11
	cmovbq	%r14, %r9
	cmpq	%r8, %r11
	movq	%r8, %r14
	cmovaq	%r11, %r14
	cmovaeq	%r8, %r11
	cmpq	%r10, %r14
	movq	%r10, %r15
	cmovaq	%r14, %r15
	cmovaeq	%r10, %r14
	cmpq	%rdi, %r15
	movq	%rdi, %r10
	cmovaq	%r15, %r10
	cmovaeq	%rdi, %r15
	cmpq	%rsi, %r10
	movq	%rsi, %r12
	cmovaq	%r10, %r12
	cmovaeq	%rsi, %r10
	cmpq	%rdx, %r12
	movq	%rdx, %rsi
	cmovaq	%r12, %rsi
	.loc	1 72 25                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:72:25 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	movq	%rcx, %rdi
	.loc	1 179 9                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:179:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	cmovaeq	%rdx, %r12
	cmpq	%rcx, %rsi
	cmovaq	%rsi, %rcx
	cmovaeq	%rdi, %rsi
	.loc	1 178 36                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:178:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	cmpq	%r9, %r8
	.loc	1 179 9                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:179:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	movq	%r11, %rdi
	cmovbq	%r9, %rdi
	cmovaeq	%r9, %r11
	cmpq	%r14, %rdi
	movq	%r14, %r8
	cmovaq	%rdi, %r8
	cmovaeq	%r14, %rdi
	cmpq	%r15, %r8
	movq	%r15, %r14
	cmovaq	%r8, %r14
	cmovaeq	%r15, %r8
	cmpq	%r10, %r14
	movq	%r10, %r15
	cmovaq	%r14, %r15
	cmovaeq	%r10, %r14
	cmpq	%r12, %r15
	movq	%r12, %r10
	cmovaq	%r15, %r10
	cmovaeq	%r12, %r15
	cmpq	%rsi, %r10
	movq	%rsi, %rdx
	cmovaq	%r10, %rdx
	cmovaeq	%rsi, %r10
	cmpq	%rdi, %r11
	movq	%rdi, %r9
	cmovaq	%r11, %r9
	cmovbq	%r11, %rdi
	cmpq	%r8, %r9
	movq	%r8, %r11
	cmovaq	%r9, %r11
	cmovaeq	%r8, %r9
	cmpq	%r14, %r11
	movq	%r14, %r12
	cmovaq	%r11, %r12
	cmovaeq	%r14, %r11
	cmpq	%r15, %r12
	movq	%r15, %r14
	cmovaq	%r12, %r14
	cmovaeq	%r15, %r12
	cmpq	%r10, %r14
	movq	%r10, %rsi
	cmovaq	%r14, %rsi
	cmovaeq	%r10, %r14
	.loc	1 178 36                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:178:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	cmpq	%rdi, %r8
	.loc	1 179 9                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:179:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	movq	%r9, %r8
	cmovbq	%rdi, %r8
	cmovaeq	%rdi, %r9
	cmpq	%r11, %r8
	movq	%r11, %r15
	cmovaq	%r8, %r15
	cmovaeq	%r11, %r8
	cmpq	%r12, %r15
	movq	%r12, %r11
	cmovaq	%r15, %r11
	cmovaeq	%r12, %r15
	cmpq	%r14, %r11
	movq	%r14, %rdi
	cmovaq	%r11, %rdi
	cmovaeq	%r14, %r11
	cmpq	%r8, %r9
	movq	%r8, %r12
	cmovaq	%r9, %r12
	cmovbq	%r9, %r8
	cmpq	%r15, %r12
	movq	%r15, %r9
	cmovaq	%r12, %r9
	cmovaeq	%r15, %r12
	cmpq	%r11, %r9
	movq	%r11, %r10
	cmovaq	%r9, %r10
	cmovaeq	%r11, %r9
	.loc	1 178 36                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:178:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	cmpq	%r8, %r15
	.loc	1 179 9                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:179:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	movq	%r12, %r14
	cmovbq	%r8, %r14
	cmovaeq	%r8, %r12
	cmpq	%r9, %r14
	movq	%r9, %r8
	cmovaq	%r14, %r8
	cmovaeq	%r9, %r14
	cmpq	%r14, %r12
	movq	%r14, %r9
	cmovaq	%r12, %r9
	cmovbq	%r12, %r14
	.loc	1 84 36                         # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:84:36 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	decq	%rax
	.loc	1 85 9                          # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:85:9 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	jne	.LBB0_4
# %bb.5:
	.loc	1 108 40                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:108:40 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	imulq	$131, %r14, %r14
	.loc	1 104 28                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:104:28 @[ tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:249:23 ]
	addq	%rcx, %r14
.Ltmp1:
.LBB0_6:
	.loc	1 250 13                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:250:13
	movq	stderr(%rip), %rdi
	.loc	1 250 5 is_stmt 0               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:250:5
	movl	$.L.str.1, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 251 5 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:251:5
	movl	$.L.str.2, %edi
	movq	%r14, %rsi
	xorl	%eax, %eax
	callq	printf
	jmp	.LBB0_7
.LBB0_1:
	.loc	1 247 62                        # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:247:62
	movq	stderr(%rip), %rcx
	.loc	1 247 22 is_stmt 0              # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:247:22
	movl	$.L.str, %edi
	movl	$29, %esi
	movl	$1, %edx
	callq	fwrite@PLT
	movl	$2, %ebx
.LBB0_7:
	.loc	1 253 1 is_stmt 1               # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:253:1
	movl	%ebx, %eax
	.loc	1 253 1 epilogue_begin is_stmt 0 # tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c:253:1
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
	.byte	249                             # DW_AT_call_line
	.byte	23                              # DW_AT_call_column
	.byte	0                               # End Of Children Mark
	.byte	0                               # End Of Children Mark
.Ldebug_info_end0:
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"tools/benchmarks/merlo/benchmarks/stage06p_codegen/bubble_sort_8/meldra.c" # string offset=1
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
