	.file	"meldra.c"
	.section	.text.unlikely.,"ax",@progbits
	.globl	main                            # -- Begin function main
	.p2align	4
	.type	main,@function
main:                                   # @main
.Lfunc_begin0:
	.file	1 "/home/manera/orca/workspaces/Bedik-1/sablefish" "benchmarks/stage06p_codegen/startup/meldra.c"
	.loc	1 46 0                          # benchmarks/stage06p_codegen/startup/meldra.c:46:0
	.cfi_startproc
# %bb.0:
	pushq	%rbx
	.cfi_def_cfa_offset 16
	.cfi_offset %rbx, -16
	.loc	1 47 14 prologue_end            # benchmarks/stage06p_codegen/startup/meldra.c:47:14
	cmpl	$2, %edi
	jne	.LBB0_1
# %bb.2:
	.loc	1 48 59                         # benchmarks/stage06p_codegen/startup/meldra.c:48:59
	movq	8(%rsi), %rdi
	xorl	%ebx, %ebx
	.loc	1 48 50 is_stmt 0               # benchmarks/stage06p_codegen/startup/meldra.c:48:50
	xorl	%esi, %esi
	movl	$10, %edx
	callq	strtoull
	.loc	1 50 13 is_stmt 1               # benchmarks/stage06p_codegen/startup/meldra.c:50:13
	movq	stderr(%rip), %rdi
	.loc	1 50 5 is_stmt 0                # benchmarks/stage06p_codegen/startup/meldra.c:50:5
	movl	$.L.str.1, %esi
	xorl	%edx, %edx
	xorl	%eax, %eax
	callq	fprintf
	.loc	1 51 5 is_stmt 1                # benchmarks/stage06p_codegen/startup/meldra.c:51:5
	movl	$.L.str.2, %edi
	movl	$42, %esi
	xorl	%eax, %eax
	callq	printf
	.loc	1 53 1                          # benchmarks/stage06p_codegen/startup/meldra.c:53:1
	movl	%ebx, %eax
	.loc	1 53 1 epilogue_begin is_stmt 0 # benchmarks/stage06p_codegen/startup/meldra.c:53:1
	popq	%rbx
	.cfi_def_cfa_offset 8
	retq
.LBB0_1:
	.cfi_def_cfa_offset 16
	.loc	1 47 62 is_stmt 1               # benchmarks/stage06p_codegen/startup/meldra.c:47:62
	movq	stderr(%rip), %rcx
	.loc	1 47 22 is_stmt 0               # benchmarks/stage06p_codegen/startup/meldra.c:47:22
	movl	$.L.str, %edi
	movl	$29, %esi
	movl	$1, %edx
	callq	fwrite@PLT
	movl	$2, %ebx
	.loc	1 53 1 is_stmt 1                # benchmarks/stage06p_codegen/startup/meldra.c:53:1
	movl	%ebx, %eax
	.loc	1 53 1 epilogue_begin is_stmt 0 # benchmarks/stage06p_codegen/startup/meldra.c:53:1
	popq	%rbx
	.cfi_def_cfa_offset 8
	retq
.Ltmp0:
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
	.asciz	"benchmarks/stage06p_codegen/startup/meldra.c" # string offset=1
.Linfo_string2:
	.asciz	"/home/manera/orca/workspaces/Bedik-1/sablefish" # string offset=46
	.section	".note.GNU-stack","",@progbits
	.addrsig
	.section	.debug_line,"",@progbits
.Lline_table_start0:
