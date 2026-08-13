	.file	"c.c"
	.text
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "benchmarks/stage06p_codegen/startup/c.c"
	.loc	1 22 0                          # benchmarks/stage06p_codegen/startup/c.c:22:0
	.cfi_startproc
# %bb.0:
	movl	$2, %eax
.Ltmp0:
	.loc	1 23 14 prologue_end            # benchmarks/stage06p_codegen/startup/c.c:23:14
	cmpl	$2, %edi
	je	.LBB0_1
# %bb.2:
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 28 1                          # benchmarks/stage06p_codegen/startup/c.c:28:1
	retq
.LBB0_1:
	.loc	1 0 1 is_stmt 0                 # benchmarks/stage06p_codegen/startup/c.c:0:1
	pushq	%rax
	.cfi_def_cfa_offset 16
	.loc	1 24 36 is_stmt 1               # benchmarks/stage06p_codegen/startup/c.c:24:36
	movq	8(%rsi), %rdi
	.loc	1 24 27 is_stmt 0               # benchmarks/stage06p_codegen/startup/c.c:24:27
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	.loc	1 25 13 is_stmt 1               # benchmarks/stage06p_codegen/startup/c.c:25:13
	movq	stderr(%rip), %rdi
	.loc	1 25 5 is_stmt 0                # benchmarks/stage06p_codegen/startup/c.c:25:5
	movl	$.L.str, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 26 5 is_stmt 1                # benchmarks/stage06p_codegen/startup/c.c:26:5
	movl	$.L.str.1, %edi
	movl	$42, %esi
	xorl	%eax, %eax
	callq	printf
	xorl	%eax, %eax
	addq	$8, %rsp
	.cfi_def_cfa_offset 8
                                        # kill: def $eax killed $eax killed $rax
	.loc	1 28 1                          # benchmarks/stage06p_codegen/startup/c.c:28:1
	retq
.Ltmp1:
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
	.byte	0                               # DW_CHILDREN_no
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
	.byte	0                               # EOM(3)
	.section	.debug_info,"",@progbits
.Lcu_begin0:
	.long	.Ldebug_info_end0-.Ldebug_info_start0 # Length of Unit
.Ldebug_info_start0:
	.short	4                               # DWARF version number
	.long	.debug_abbrev                   # Offset Into Abbrev. Section
	.byte	8                               # Address Size (in bytes)
	.byte	1                               # Abbrev [1] 0xb:0x1f DW_TAG_compile_unit
	.long	.Linfo_string0                  # DW_AT_producer
	.short	29                              # DW_AT_language
	.long	.Linfo_string1                  # DW_AT_name
	.long	.Lline_table_start0             # DW_AT_stmt_list
	.long	.Linfo_string2                  # DW_AT_comp_dir
	.quad	.Lfunc_begin0                   # DW_AT_low_pc
	.long	.Lfunc_end0-.Lfunc_begin0       # DW_AT_high_pc
.Ldebug_info_end0:
	.section	.debug_str,"MS",@progbits,1
.Linfo_string0:
	.byte	0                               # string offset=0
.Linfo_string1:
	.asciz	"benchmarks/stage06p_codegen/startup/c.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=41
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
