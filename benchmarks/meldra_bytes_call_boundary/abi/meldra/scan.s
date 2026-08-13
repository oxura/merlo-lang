
benchmarks/meldra_bytes_call_boundary/abi/meldra/program:     file format elf64-x86-64


Disassembly of section .init:

00000000004002e0 <_init>:
  4002e0:	endbr64
  4002e4:	sub    $0x8,%rsp
  4002e8:	mov    0x3cf1(%rip),%rax        # 403fe0 <__gmon_start__>
  4002ef:	test   %rax,%rax
  4002f2:	je     4002f6 <_init+0x16>
  4002f4:	call   *%rax
  4002f6:	add    $0x8,%rsp
  4002fa:	ret

Disassembly of section .plt:

0000000000400300 <free@plt-0x10>:
  400300:	push   0x3cea(%rip)        # 403ff0 <_GLOBAL_OFFSET_TABLE_+0x8>
  400306:	jmp    *0x3cec(%rip)        # 403ff8 <_GLOBAL_OFFSET_TABLE_+0x10>
  40030c:	nopl   0x0(%rax)

0000000000400310 <free@plt>:
  400310:	jmp    *0x3cea(%rip)        # 404000 <free@GLIBC_2.2.5>
  400316:	push   $0x0
  40031b:	jmp    400300 <_init+0x20>

0000000000400320 <abort@plt>:
  400320:	jmp    *0x3ce2(%rip)        # 404008 <abort@GLIBC_2.2.5>
  400326:	push   $0x1
  40032b:	jmp    400300 <_init+0x20>

0000000000400330 <printf@plt>:
  400330:	jmp    *0x3cda(%rip)        # 404010 <printf@GLIBC_2.2.5>
  400336:	push   $0x2
  40033b:	jmp    400300 <_init+0x20>

0000000000400340 <strtoull@plt>:
  400340:	jmp    *0x3cd2(%rip)        # 404018 <strtoull@GLIBC_2.2.5>
  400346:	push   $0x3
  40034b:	jmp    400300 <_init+0x20>

0000000000400350 <fprintf@plt>:
  400350:	jmp    *0x3cca(%rip)        # 404020 <fprintf@GLIBC_2.2.5>
  400356:	push   $0x4
  40035b:	jmp    400300 <_init+0x20>

0000000000400360 <malloc@plt>:
  400360:	jmp    *0x3cc2(%rip)        # 404028 <malloc@GLIBC_2.2.5>
  400366:	push   $0x5
  40036b:	jmp    400300 <_init+0x20>

0000000000400370 <fwrite@plt>:
  400370:	jmp    *0x3cba(%rip)        # 404030 <fwrite@GLIBC_2.2.5>
  400376:	push   $0x6
  40037b:	jmp    400300 <_init+0x20>

Disassembly of section .text:

0000000000400380 <meldra_panic_bytes_allocation_overflow>:
  400380:	push   %rax
  400381:	mov    %rdi,%rdx
  400384:	mov    0x3cb5(%rip),%rdi        # 404040 <stderr@GLIBC_2.2.5>
  40038b:	mov    $0x402466,%esi
  400390:	xor    %eax,%eax
  400392:	call   400350 <fprintf@plt>
  400397:	call   400320 <abort@plt>
  40039c:	nopl   0x0(%rax)

00000000004003a0 <meldra_panic_alloc>:
  4003a0:	push   %rax
  4003a1:	mov    0x3c98(%rip),%rcx        # 404040 <stderr@GLIBC_2.2.5>
  4003a8:	mov    $0x402484,%edi
  4003ad:	mov    $0x1a,%esi
  4003b2:	mov    $0x1,%edx
  4003b7:	call   400370 <fwrite@plt>
  4003bc:	call   400320 <abort@plt>
  4003c1:	data16 data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)

00000000004003d0 <meldra_panic_bytes_bounds>:
  4003d0:	push   %rax
  4003d1:	mov    %rsi,%rcx
  4003d4:	mov    %rdi,%rdx
  4003d7:	mov    0x3c62(%rip),%rdi        # 404040 <stderr@GLIBC_2.2.5>
  4003de:	mov    $0x40249f,%esi
  4003e3:	xor    %eax,%eax
  4003e5:	call   400350 <fprintf@plt>
  4003ea:	call   400320 <abort@plt>
  4003ef:	nop

00000000004003f0 <meldra_panic_bytes_slice>:
  4003f0:	push   %rax
  4003f1:	mov    %rdx,%r8
  4003f4:	mov    %rsi,%rcx
  4003f7:	mov    %rdi,%rdx
  4003fa:	mov    0x3c3f(%rip),%rdi        # 404040 <stderr@GLIBC_2.2.5>
  400401:	mov    $0x4024db,%esi
  400406:	xor    %eax,%eax
  400408:	call   400350 <fprintf@plt>
  40040d:	call   400320 <abort@plt>
  400412:	data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)

0000000000400420 <meldra_panic_bytes_double_drop>:
  400420:	push   %rax
  400421:	mov    0x3c18(%rip),%rcx        # 404040 <stderr@GLIBC_2.2.5>
  400428:	mov    $0x402512,%edi
  40042d:	mov    $0x10,%esi
  400432:	mov    $0x1,%edx
  400437:	call   400370 <fwrite@plt>
  40043c:	call   400320 <abort@plt>
  400441:	data16 data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)

0000000000400450 <meldra_panic_division>:
  400450:	push   %rax
  400451:	mov    0x3be8(%rip),%rcx        # 404040 <stderr@GLIBC_2.2.5>
  400458:	mov    $0x4024c2,%edi
  40045d:	mov    $0x18,%esi
  400462:	mov    $0x1,%edx
  400467:	call   400370 <fwrite@plt>
  40046c:	call   400320 <abort@plt>
  400471:	cs nopw 0x0(%rax,%rax,1)
  40047b:	nopl   0x0(%rax,%rax,1)

0000000000400480 <_start>:
  400480:	endbr64
  400484:	xor    %ebp,%ebp
  400486:	mov    %rdx,%r9
  400489:	pop    %rsi
  40048a:	mov    %rsp,%rdx
  40048d:	and    $0xfffffffffffffff0,%rsp
  400491:	push   %rax
  400492:	push   %rsp
  400493:	xor    %r8d,%r8d
  400496:	xor    %ecx,%ecx
  400498:	mov    $0x400570,%rdi
  40049f:	call   *0x3b33(%rip)        # 403fd8 <__libc_start_main@GLIBC_2.34>
  4004a5:	hlt
  4004a6:	cs nopw 0x0(%rax,%rax,1)

00000000004004b0 <_dl_relocate_static_pie>:
  4004b0:	endbr64
  4004b4:	ret
  4004b5:	cs nopw 0x0(%rax,%rax,1)
  4004bf:	nop

00000000004004c0 <deregister_tm_clones>:
  4004c0:	mov    $0x404040,%eax
  4004c5:	cmp    $0x404040,%rax
  4004cb:	je     4004e0 <deregister_tm_clones+0x20>
  4004cd:	mov    $0x0,%eax
  4004d2:	test   %rax,%rax
  4004d5:	je     4004e0 <deregister_tm_clones+0x20>
  4004d7:	mov    $0x404040,%edi
  4004dc:	jmp    *%rax
  4004de:	xchg   %ax,%ax
  4004e0:	ret
  4004e1:	nopl   0x0(%rax)
  4004e5:	data16 cs nopw 0x0(%rax,%rax,1)

00000000004004f0 <register_tm_clones>:
  4004f0:	mov    $0x404040,%esi
  4004f5:	sub    $0x404040,%rsi
  4004fc:	mov    %rsi,%rax
  4004ff:	shr    $0x3f,%rsi
  400503:	sar    $0x3,%rax
  400507:	add    %rax,%rsi
  40050a:	sar    $1,%rsi
  40050d:	je     400520 <register_tm_clones+0x30>
  40050f:	mov    $0x0,%eax
  400514:	test   %rax,%rax
  400517:	je     400520 <register_tm_clones+0x30>
  400519:	mov    $0x404040,%edi
  40051e:	jmp    *%rax
  400520:	ret
  400521:	nopl   0x0(%rax)
  400525:	data16 cs nopw 0x0(%rax,%rax,1)

0000000000400530 <__do_global_dtors_aux>:
  400530:	endbr64
  400534:	cmpb   $0x0,0x3b0d(%rip)        # 404048 <completed.0>
  40053b:	jne    400550 <__do_global_dtors_aux+0x20>
  40053d:	push   %rbp
  40053e:	mov    %rsp,%rbp
  400541:	call   4004c0 <deregister_tm_clones>
  400546:	movb   $0x1,0x3afb(%rip)        # 404048 <completed.0>
  40054d:	pop    %rbp
  40054e:	ret
  40054f:	nop
  400550:	ret
  400551:	nopl   0x0(%rax)
  400555:	data16 cs nopw 0x0(%rax,%rax,1)

0000000000400560 <frame_dummy>:
  400560:	endbr64
  400564:	jmp    4004f0 <register_tm_clones>
  400566:	cs nopw 0x0(%rax,%rax,1)

0000000000400570 <main>:
  400570:	push   %rbp
  400571:	push   %r15
  400573:	push   %r14
  400575:	push   %r13
  400577:	push   %r12
  400579:	push   %rbx
  40057a:	sub    $0xc8,%rsp
  400581:	cmp    $0x6,%edi
  400584:	jne    400632 <main+0xc2>
  40058a:	mov    0x8(%rsi),%rdi
  40058e:	mov    %rsi,%rbx
  400591:	xor    %esi,%esi
  400593:	mov    $0xa,%edx
  400598:	call   400340 <strtoull@plt>
  40059d:	mov    %rax,%rbp
  4005a0:	mov    0x10(%rbx),%rdi
  4005a4:	xor    %esi,%esi
  4005a6:	mov    $0xa,%edx
  4005ab:	call   400340 <strtoull@plt>
  4005b0:	mov    %rax,%r12
  4005b3:	mov    0x18(%rbx),%rdi
  4005b7:	xor    %esi,%esi
  4005b9:	mov    $0xa,%edx
  4005be:	call   400340 <strtoull@plt>
  4005c3:	mov    %rax,0x58(%rsp)
  4005c8:	mov    0x20(%rbx),%rdi
  4005cc:	xor    %esi,%esi
  4005ce:	mov    $0xa,%edx
  4005d3:	call   400340 <strtoull@plt>
  4005d8:	mov    %rax,%r14
  4005db:	mov    0x28(%rbx),%rdi
  4005df:	xor    %esi,%esi
  4005e1:	mov    $0xa,%edx
  4005e6:	call   400340 <strtoull@plt>
  4005eb:	test   %rbp,%rbp
  4005ee:	js     400db9 <main+0x849>
  4005f4:	mov    %rax,%rsi
  4005f7:	movl   $0x0,0x3b(%rsp)
  4005ff:	movl   $0x0,0x38(%rsp)
  400607:	mov    %rax,0x30(%rsp)
  40060c:	mov    %r14,0x50(%rsp)
  400611:	jne    400658 <main+0xe8>
  400613:	movq   $0x0,0x40(%rsp)
  40061c:	mov    %r12,0x60(%rsp)
  400621:	cmpq   $0x0,0x58(%rsp)
  400627:	jne    400abc <main+0x54c>
  40062d:	jmp    400c6a <main+0x6fa>
  400632:	mov    0x3a07(%rip),%rcx        # 404040 <stderr@GLIBC_2.2.5>
  400639:	mov    $0x4023d0,%edi
  40063e:	mov    $0x1d,%esi
  400643:	mov    $0x1,%edx
  400648:	call   400370 <fwrite@plt>
  40064d:	mov    $0x2,%r14d
  400653:	jmp    400d87 <main+0x817>
  400658:	mov    %rbp,%rdi
  40065b:	call   400360 <malloc@plt>
  400660:	test   %rax,%rax
  400663:	je     400dce <main+0x85e>
  400669:	incq   0x39e0(%rip)        # 404050 <meldra_heap_allocations>
  400670:	add    %rbp,0x39e9(%rip)        # 404060 <meldra_allocated_bytes>
  400677:	mov    0x39ea(%rip),%rdi        # 404068 <meldra_bounds_checks>
  40067e:	cmp    $0x8,%rbp
  400682:	jae    40068b <main+0x11b>
  400684:	xor    %ecx,%ecx
  400686:	jmp    400a65 <main+0x4f5>
  40068b:	movabs $0x7ffffffffffffff0,%rdx
  400695:	movq   %r12,%xmm0
  40069a:	cmp    $0x10,%rbp
  40069e:	jae    4007f8 <main+0x288>
  4006a4:	xor    %ecx,%ecx
  4006a6:	mov    %rcx,%rsi
  4006a9:	add    $0x8,%rdx
  4006ad:	mov    %rdx,%rcx
  4006b0:	and    %rbp,%rcx
  4006b3:	pshufd $0x44,%xmm0,%xmm0
  4006b8:	movq   %rsi,%xmm1
  4006bd:	pshufd $0x44,%xmm1,%xmm1
  4006c2:	movdqa 0x1c86(%rip),%xmm2        # 402350 <__dso_handle+0x48>
  4006ca:	por    %xmm1,%xmm2
  4006ce:	movdqa 0x1c8a(%rip),%xmm3        # 402360 <__dso_handle+0x58>
  4006d6:	por    %xmm1,%xmm3
  4006da:	movdqa 0x1c8e(%rip),%xmm4        # 402370 <__dso_handle+0x68>
  4006e2:	por    %xmm1,%xmm4
  4006e6:	por    0x1c92(%rip),%xmm1        # 402380 <__dso_handle+0x78>
  4006ee:	movdqa 0x1c9a(%rip),%xmm5        # 402390 <__dso_handle+0x88>
  4006f6:	movdqa 0x1cb2(%rip),%xmm6        # 4023b0 <__dso_handle+0xa8>
  4006fe:	xchg   %ax,%ax
  400700:	movdqa %xmm1,%xmm9
  400705:	psrlq  $0x3,%xmm9
  40070b:	movdqa %xmm4,%xmm10
  400710:	psrlq  $0x3,%xmm10
  400716:	movdqa %xmm3,%xmm11
  40071b:	psrlq  $0x3,%xmm11
  400721:	movdqa %xmm2,%xmm7
  400725:	psrlq  $0x3,%xmm7
  40072a:	movdqa %xmm2,%xmm12
  40072f:	psllq  $0x4,%xmm12
  400735:	paddq  %xmm2,%xmm12
  40073a:	movdqa %xmm3,%xmm13
  40073f:	psllq  $0x4,%xmm13
  400745:	movdqa %xmm4,%xmm14
  40074a:	psllq  $0x4,%xmm14
  400750:	movdqa %xmm1,%xmm15
  400755:	psllq  $0x4,%xmm15
  40075b:	movdqa %xmm1,%xmm8
  400760:	paddq  %xmm0,%xmm8
  400765:	paddq  %xmm15,%xmm8
  40076a:	paddq  %xmm9,%xmm8
  40076f:	movdqa %xmm4,%xmm9
  400774:	paddq  %xmm0,%xmm9
  400779:	paddq  %xmm14,%xmm9
  40077e:	paddq  %xmm10,%xmm9
  400783:	movdqa %xmm3,%xmm10
  400788:	paddq  %xmm0,%xmm10
  40078d:	paddq  %xmm13,%xmm10
  400792:	paddq  %xmm11,%xmm10
  400797:	paddq  %xmm0,%xmm7
  40079b:	paddq  %xmm12,%xmm7
  4007a0:	pand   %xmm5,%xmm8
  4007a5:	pand   %xmm5,%xmm9
  4007aa:	packuswb %xmm9,%xmm8
  4007af:	pand   %xmm5,%xmm10
  4007b4:	pand   %xmm5,%xmm7
  4007b8:	packuswb %xmm7,%xmm10
  4007bd:	packuswb %xmm10,%xmm8
  4007c2:	packuswb %xmm8,%xmm8
  4007c7:	movq   %xmm8,(%rax,%rsi,1)
  4007cd:	add    $0x8,%rsi
  4007d1:	paddq  %xmm6,%xmm1
  4007d5:	paddq  %xmm6,%xmm4
  4007d9:	paddq  %xmm6,%xmm3
  4007dd:	paddq  %xmm6,%xmm2
  4007e1:	cmp    %rsi,%rcx
  4007e4:	jne    400700 <main+0x190>
  4007ea:	cmp    %rcx,%rbp
  4007ed:	jne    400a65 <main+0x4f5>
  4007f3:	jmp    400a97 <main+0x527>
  4007f8:	mov    %rbp,%rcx
  4007fb:	and    %rdx,%rcx
  4007fe:	movdqa %xmm0,0x90(%rsp)
  400807:	pshufd $0x44,%xmm0,%xmm0
  40080c:	movdqa %xmm0,0x60(%rsp)
  400812:	movdqa 0x1af5(%rip),%xmm13        # 402310 <__dso_handle+0x8>
  40081b:	movdqa 0x1afd(%rip),%xmm2        # 402320 <__dso_handle+0x18>
  400823:	movdqa 0x1b05(%rip),%xmm3        # 402330 <__dso_handle+0x28>
  40082b:	movdqa 0x1b0c(%rip),%xmm11        # 402340 <__dso_handle+0x38>
  400834:	movdqa 0x1b14(%rip),%xmm6        # 402350 <__dso_handle+0x48>
  40083c:	movdqa 0x1b1c(%rip),%xmm1        # 402360 <__dso_handle+0x58>
  400844:	movdqa 0x1b24(%rip),%xmm0        # 402370 <__dso_handle+0x68>
  40084c:	movdqa 0x1b2b(%rip),%xmm9        # 402380 <__dso_handle+0x78>
  400855:	xor    %esi,%esi
  400857:	nopw   0x0(%rax,%rax,1)
  400860:	movdqa %xmm3,0x20(%rsp)
  400866:	movdqa %xmm2,0x40(%rsp)
  40086c:	movdqa %xmm9,%xmm8
  400871:	psrlq  $0x3,%xmm8
  400877:	movdqa %xmm0,%xmm7
  40087b:	psrlq  $0x3,%xmm7
  400880:	movdqa %xmm1,%xmm10
  400885:	psrlq  $0x3,%xmm10
  40088b:	movdqa %xmm6,%xmm5
  40088f:	psrlq  $0x3,%xmm5
  400894:	movdqa %xmm11,%xmm15
  400899:	psrlq  $0x3,%xmm15
  40089f:	movdqa 0x20(%rsp),%xmm14
  4008a6:	psrlq  $0x3,%xmm14
  4008ac:	movdqa %xmm2,%xmm4
  4008b0:	psrlq  $0x3,%xmm4
  4008b5:	movdqa %xmm9,%xmm2
  4008ba:	psllq  $0x4,%xmm2
  4008bf:	movdqa %xmm9,%xmm12
  4008c4:	movdqa 0x60(%rsp),%xmm3
  4008ca:	paddq  %xmm3,%xmm12
  4008cf:	paddq  %xmm2,%xmm12
  4008d4:	movdqa %xmm0,%xmm2
  4008d8:	psllq  $0x4,%xmm2
  4008dd:	paddq  %xmm8,%xmm12
  4008e2:	movdqa %xmm0,%xmm8
  4008e7:	paddq  %xmm3,%xmm8
  4008ec:	paddq  %xmm2,%xmm8
  4008f1:	movdqa %xmm1,%xmm2
  4008f5:	psllq  $0x4,%xmm2
  4008fa:	paddq  %xmm7,%xmm8
  4008ff:	movdqa %xmm1,%xmm7
  400903:	paddq  %xmm3,%xmm7
  400907:	paddq  %xmm2,%xmm7
  40090b:	movdqa %xmm6,%xmm2
  40090f:	psllq  $0x4,%xmm2
  400914:	paddq  %xmm10,%xmm7
  400919:	movdqa %xmm6,%xmm10
  40091e:	paddq  %xmm3,%xmm10
  400923:	paddq  %xmm2,%xmm10
  400928:	movdqa %xmm11,%xmm2
  40092d:	psllq  $0x4,%xmm2
  400932:	paddq  %xmm5,%xmm10
  400937:	movdqa %xmm11,%xmm5
  40093c:	paddq  %xmm3,%xmm5
  400940:	paddq  %xmm2,%xmm5
  400944:	movdqa 0x20(%rsp),%xmm2
  40094a:	psllq  $0x4,%xmm2
  40094f:	paddq  %xmm15,%xmm5
  400954:	movdqa 0x20(%rsp),%xmm15
  40095b:	paddq  %xmm3,%xmm15
  400960:	paddq  %xmm2,%xmm15
  400965:	movdqa 0x40(%rsp),%xmm2
  40096b:	psllq  $0x4,%xmm2
  400970:	paddq  %xmm14,%xmm15
  400975:	movdqa 0x40(%rsp),%xmm14
  40097c:	paddq  %xmm3,%xmm14
  400981:	paddq  %xmm2,%xmm14
  400986:	movdqa %xmm13,%xmm2
  40098b:	psrlq  $0x3,%xmm2
  400990:	paddq  %xmm4,%xmm14
  400995:	movdqa %xmm13,%xmm4
  40099a:	psllq  $0x4,%xmm13
  4009a0:	paddq  %xmm4,%xmm13
  4009a5:	paddq  %xmm3,%xmm2
  4009a9:	paddq  %xmm13,%xmm2
  4009ae:	movdqa %xmm4,%xmm13
  4009b3:	movdqa 0x19d5(%rip),%xmm3        # 402390 <__dso_handle+0x88>
  4009bb:	pand   %xmm3,%xmm12
  4009c0:	pand   %xmm3,%xmm8
  4009c5:	packuswb %xmm8,%xmm12
  4009ca:	pand   %xmm3,%xmm7
  4009ce:	pand   %xmm3,%xmm10
  4009d3:	packuswb %xmm10,%xmm7
  4009d8:	packuswb %xmm7,%xmm12
  4009dd:	pand   %xmm3,%xmm5
  4009e1:	pand   %xmm3,%xmm15
  4009e6:	packuswb %xmm15,%xmm5
  4009eb:	pand   %xmm3,%xmm14
  4009f0:	pand   %xmm3,%xmm2
  4009f4:	packuswb %xmm2,%xmm14
  4009f9:	movdqa 0x40(%rsp),%xmm2
  4009ff:	packuswb %xmm14,%xmm5
  400a04:	movdqa 0x20(%rsp),%xmm3
  400a0a:	packuswb %xmm5,%xmm12
  400a0f:	movdqu %xmm12,(%rax,%rsi,1)
  400a15:	add    $0x10,%rsi
  400a19:	movdqa 0x197f(%rip),%xmm4        # 4023a0 <__dso_handle+0x98>
  400a21:	paddq  %xmm4,%xmm9
  400a26:	paddq  %xmm4,%xmm0
  400a2a:	paddq  %xmm4,%xmm1
  400a2e:	paddq  %xmm4,%xmm6
  400a32:	paddq  %xmm4,%xmm11
  400a37:	paddq  %xmm4,%xmm3
  400a3b:	paddq  %xmm4,%xmm2
  400a3f:	paddq  %xmm4,%xmm13
  400a44:	cmp    %rsi,%rcx
  400a47:	jne    400860 <main+0x2f0>
  400a4d:	cmp    %rcx,%rbp
  400a50:	movdqa 0x90(%rsp),%xmm0
  400a59:	je     400a97 <main+0x527>
  400a5b:	test   $0x8,%bpl
  400a5f:	jne    4006a6 <main+0x136>
  400a65:	mov    %ecx,%esi
  400a67:	shl    $0x4,%esi
  400a6a:	add    %ecx,%esi
  400a6c:	mov    %r12d,%edx
  400a6f:	add    %sil,%dl
  400a72:	data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  400a80:	mov    %ecx,%esi
  400a82:	shr    $0x3,%esi
  400a85:	add    %dl,%sil
  400a88:	mov    %sil,(%rax,%rcx,1)
  400a8c:	inc    %rcx
  400a8f:	add    $0x11,%dl
  400a92:	cmp    %rcx,%rbp
  400a95:	jne    400a80 <main+0x510>
  400a97:	mov    %rax,0x40(%rsp)
  400a9c:	add    %rbp,%rdi
  400a9f:	mov    %rdi,0x35c2(%rip)        # 404068 <meldra_bounds_checks>
  400aa6:	mov    0x30(%rsp),%rsi
  400aab:	mov    %r12,0x60(%rsp)
  400ab0:	cmpq   $0x0,0x58(%rsp)
  400ab6:	je     400c6a <main+0x6fa>
  400abc:	mov    %rbp,%rbx
  400abf:	sub    %rsi,%rbx
  400ac2:	inc    %rbx
  400ac5:	je     400dd3 <main+0x863>
  400acb:	cmpq   $0x0,0x40(%rsp)
  400ad1:	mov    %rbp,0x20(%rsp)
  400ad6:	jne    400b9e <main+0x62e>
  400adc:	xor    %r14d,%r14d
  400adf:	mov    0x50(%rsp),%r13
  400ae4:	mov    0x60(%rsp),%r12
  400ae9:	nopl   0x0(%rax)
  400af0:	mov    %r13,%rax
  400af3:	or     %rbx,%rax
  400af6:	shr    $0x20,%rax
  400afa:	je     400b20 <main+0x5b0>
  400afc:	mov    %r13,%rax
  400aff:	xor    %edx,%edx
  400b01:	div    %rbx
  400b04:	mov    %rdx,%r15
  400b07:	mov    %rbp,%rax
  400b0a:	sub    %r15,%rax
  400b0d:	jae    400b36 <main+0x5c6>
  400b0f:	jmp    400dae <main+0x83e>
  400b14:	data16 data16 cs nopw 0x0(%rax,%rax,1)
  400b20:	mov    %r13d,%eax
  400b23:	xor    %edx,%edx
  400b25:	div    %ebx
  400b27:	mov    %edx,%r15d
  400b2a:	mov    %rbp,%rax
  400b2d:	sub    %r15,%rax
  400b30:	jb     400dae <main+0x83e>
  400b36:	cmp    %rax,%rsi
  400b39:	ja     400dae <main+0x83e>
  400b3f:	xor    %edi,%edi
  400b41:	mov    %r12,%rdx
  400b44:	call   400de0 <meldra_fn_scan>
  400b49:	mov    %rax,%r12
  400b4c:	mov    0x3515(%rip),%rax        # 404068 <meldra_bounds_checks>
  400b53:	lea    0x1(%rax),%rcx
  400b57:	mov    %rcx,0x350a(%rip)        # 404068 <meldra_bounds_checks>
  400b5e:	cmp    %rbp,%r15
  400b61:	jae    400d9c <main+0x82c>
  400b67:	add    $0x2,%rax
  400b6b:	movzbl (%r15),%ecx
  400b6f:	add    %r12b,%cl
  400b72:	mov    %rax,0x34ef(%rip)        # 404068 <meldra_bounds_checks>
  400b79:	movzbl %cl,%eax
  400b7c:	add    %r14d,%eax
  400b7f:	mov    %al,(%r15)
  400b82:	inc    %r14
  400b85:	add    $0x61,%r13
  400b89:	cmp    %r14,0x58(%rsp)
  400b8e:	mov    0x30(%rsp),%rsi
  400b93:	jne    400af0 <main+0x580>
  400b99:	jmp    400c6a <main+0x6fa>
  400b9e:	xor    %r14d,%r14d
  400ba1:	mov    0x50(%rsp),%r13
  400ba6:	mov    0x60(%rsp),%r12
  400bab:	nopl   0x0(%rax,%rax,1)
  400bb0:	mov    %r13,%rax
  400bb3:	or     %rbx,%rax
  400bb6:	shr    $0x20,%rax
  400bba:	je     400be0 <main+0x670>
  400bbc:	mov    %r13,%rax
  400bbf:	xor    %edx,%edx
  400bc1:	div    %rbx
  400bc4:	mov    %rdx,%r15
  400bc7:	mov    %rbp,%rax
  400bca:	sub    %r15,%rax
  400bcd:	jae    400bf6 <main+0x686>
  400bcf:	jmp    400dae <main+0x83e>
  400bd4:	data16 data16 cs nopw 0x0(%rax,%rax,1)
  400be0:	mov    %r13d,%eax
  400be3:	xor    %edx,%edx
  400be5:	div    %ebx
  400be7:	mov    %edx,%r15d
  400bea:	mov    %rbp,%rax
  400bed:	sub    %r15,%rax
  400bf0:	jb     400dae <main+0x83e>
  400bf6:	cmp    %rax,%rsi
  400bf9:	ja     400da9 <main+0x839>
  400bff:	mov    0x40(%rsp),%rax
  400c04:	lea    (%rax,%r15,1),%rbp
  400c08:	mov    %rbp,%rdi
  400c0b:	mov    %r12,%rdx
  400c0e:	call   400de0 <meldra_fn_scan>
  400c13:	mov    %rax,%r12
  400c16:	mov    0x344b(%rip),%rax        # 404068 <meldra_bounds_checks>
  400c1d:	lea    0x1(%rax),%rcx
  400c21:	mov    %rcx,0x3440(%rip)        # 404068 <meldra_bounds_checks>
  400c28:	cmp    0x20(%rsp),%r15
  400c2d:	jae    400d9c <main+0x82c>
  400c33:	add    $0x2,%rax
  400c37:	movzbl 0x0(%rbp),%ecx
  400c3b:	add    %r12b,%cl
  400c3e:	mov    %rax,0x3423(%rip)        # 404068 <meldra_bounds_checks>
  400c45:	movzbl %cl,%eax
  400c48:	add    %r14d,%eax
  400c4b:	mov    %al,0x0(%rbp)
  400c4e:	inc    %r14
  400c51:	add    $0x61,%r13
  400c55:	cmp    %r14,0x58(%rsp)
  400c5a:	mov    0x20(%rsp),%rbp
  400c5f:	mov    0x30(%rsp),%rsi
  400c64:	jne    400bb0 <main+0x640>
  400c6a:	mov    0x40(%rsp),%rax
  400c6f:	mov    %rax,0x70(%rsp)
  400c74:	mov    %rbp,0x78(%rsp)
  400c79:	mov    %rbp,0x80(%rsp)
  400c81:	movb   $0x1,0x88(%rsp)
  400c89:	mov    0x38(%rsp),%eax
  400c8d:	mov    0x3b(%rsp),%ecx
  400c91:	mov    %eax,0x89(%rsp)
  400c98:	mov    %ecx,0x8c(%rsp)
  400c9f:	movups 0x70(%rsp),%xmm0
  400ca4:	movups 0x80(%rsp),%xmm1
  400cac:	movups %xmm1,0x10(%rsp)
  400cb1:	movups %xmm0,(%rsp)
  400cb5:	lea    0xa8(%rsp),%rdi
  400cbd:	mov    0x60(%rsp),%rsi
  400cc2:	call   400ec0 <meldra_fn_transform>
  400cc7:	mov    0x30(%rsp),%rsi
  400ccc:	mov    0xb0(%rsp),%r15
  400cd4:	mov    %r15,%rax
  400cd7:	mov    0x50(%rsp),%rdi
  400cdc:	sub    %rdi,%rax
  400cdf:	jb     400dc1 <main+0x851>
  400ce5:	cmp    %rax,%rsi
  400ce8:	ja     400dc1 <main+0x851>
  400cee:	mov    0xa8(%rsp),%r13
  400cf6:	add    %r13,%rdi
  400cf9:	test   %r13,%r13
  400cfc:	cmove  %r13,%rdi
  400d00:	mov    %r12,%rdx
  400d03:	call   400de0 <meldra_fn_scan>
  400d08:	cmpb   $0x0,0xc0(%rsp)
  400d10:	je     400dc9 <main+0x859>
  400d16:	mov    %rax,%rbx
  400d19:	test   %r13,%r13
  400d1c:	je     400d2d <main+0x7bd>
  400d1e:	mov    %r13,%rdi
  400d21:	call   400310 <free@plt>
  400d26:	incq   0x332b(%rip)        # 404058 <meldra_heap_frees>
  400d2d:	add    %r15,%rbx
  400d30:	mov    0x3309(%rip),%rdi        # 404040 <stderr@GLIBC_2.2.5>
  400d37:	mov    0x3312(%rip),%rdx        # 404050 <meldra_heap_allocations>
  400d3e:	xor    %r14d,%r14d
  400d41:	mov    $0x4023ee,%esi
  400d46:	xor    %eax,%eax
  400d48:	call   400350 <fprintf@plt>
  400d4d:	mov    0x32ec(%rip),%rdi        # 404040 <stderr@GLIBC_2.2.5>
  400d54:	mov    0x32fd(%rip),%rdx        # 404058 <meldra_heap_frees>
  400d5b:	mov    0x32fe(%rip),%rcx        # 404060 <meldra_allocated_bytes>
  400d62:	mov    0x32ff(%rip),%r9        # 404068 <meldra_bounds_checks>
  400d69:	mov    $0x402406,%esi
  400d6e:	xor    %r8d,%r8d
  400d71:	xor    %eax,%eax
  400d73:	call   400350 <fprintf@plt>
  400d78:	mov    $0x40247f,%edi
  400d7d:	mov    %rbx,%rsi
  400d80:	xor    %eax,%eax
  400d82:	call   400330 <printf@plt>
  400d87:	mov    %r14d,%eax
  400d8a:	add    $0xc8,%rsp
  400d91:	pop    %rbx
  400d92:	pop    %r12
  400d94:	pop    %r13
  400d96:	pop    %r14
  400d98:	pop    %r15
  400d9a:	pop    %rbp
  400d9b:	ret
  400d9c:	mov    %r15,%rdi
  400d9f:	mov    0x20(%rsp),%rsi
  400da4:	call   4003d0 <meldra_panic_bytes_bounds>
  400da9:	mov    0x20(%rsp),%rbp
  400dae:	mov    %r15,%rdi
  400db1:	mov    %rbp,%rdx
  400db4:	call   4003f0 <meldra_panic_bytes_slice>
  400db9:	mov    %rbp,%rdi
  400dbc:	call   400380 <meldra_panic_bytes_allocation_overflow>
  400dc1:	mov    %r15,%rdx
  400dc4:	call   4003f0 <meldra_panic_bytes_slice>
  400dc9:	call   400420 <meldra_panic_bytes_double_drop>
  400dce:	call   4003a0 <meldra_panic_alloc>
  400dd3:	call   400450 <meldra_panic_division>
  400dd8:	nopl   0x0(%rax,%rax,1)

0000000000400de0 <meldra_fn_scan>:
  400de0:	mov    %rdx,%rax
  400de3:	test   %rsi,%rsi
  400de6:	je     400eb5 <meldra_fn_scan+0xd5>
  400dec:	mov    0x3275(%rip),%rcx        # 404068 <meldra_bounds_checks>
  400df3:	movabs $0x100000001b3,%rdx
  400dfd:	mov    %esi,%r8d
  400e00:	and    $0x3,%r8d
  400e04:	cmp    $0x4,%rsi
  400e08:	jae    400e0f <meldra_fn_scan+0x2f>
  400e0a:	xor    %r9d,%r9d
  400e0d:	jmp    400e7c <meldra_fn_scan+0x9c>
  400e0f:	mov    %rsi,%r10
  400e12:	and    $0xfffffffffffffffc,%r10
  400e16:	xor    %r9d,%r9d
  400e19:	nopl   0x0(%rax)
  400e20:	movzbl (%rdi,%r9,1),%r11d
  400e25:	add    %r9,%r11
  400e28:	inc    %r11
  400e2b:	xor    %rax,%r11
  400e2e:	imul   %rdx,%r11
  400e32:	movzbl 0x1(%rdi,%r9,1),%eax
  400e38:	add    %r9,%rax
  400e3b:	add    $0x2,%rax
  400e3f:	xor    %r11,%rax
  400e42:	imul   %rdx,%rax
  400e46:	movzbl 0x2(%rdi,%r9,1),%r11d
  400e4c:	add    %r9,%r11
  400e4f:	add    $0x3,%r11
  400e53:	xor    %rax,%r11
  400e56:	imul   %rdx,%r11
  400e5a:	movzbl 0x3(%rdi,%r9,1),%eax
  400e60:	add    %r9,%rax
  400e63:	add    $0x4,%rax
  400e67:	add    $0x4,%r9
  400e6b:	xor    %r11,%rax
  400e6e:	imul   %rdx,%rax
  400e72:	cmp    %r9,%r10
  400e75:	jne    400e20 <meldra_fn_scan+0x40>
  400e77:	test   %r8,%r8
  400e7a:	je     400eab <meldra_fn_scan+0xcb>
  400e7c:	inc    %r9
  400e7f:	mov    %rax,%r10
  400e82:	data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  400e90:	movzbl -0x1(%rdi,%r9,1),%eax
  400e96:	add    %r9,%rax
  400e99:	xor    %r10,%rax
  400e9c:	imul   %rdx,%rax
  400ea0:	inc    %r9
  400ea3:	mov    %rax,%r10
  400ea6:	dec    %r8
  400ea9:	jne    400e90 <meldra_fn_scan+0xb0>
  400eab:	add    %rsi,%rcx
  400eae:	mov    %rcx,0x31b3(%rip)        # 404068 <meldra_bounds_checks>
  400eb5:	ret
  400eb6:	cs nopw 0x0(%rax,%rax,1)

0000000000400ec0 <meldra_fn_transform>:
  400ec0:	lea    0x8(%rsp),%rax
  400ec5:	mov    0x10(%rsp),%rcx
  400eca:	test   %rcx,%rcx
  400ecd:	je     40111b <meldra_fn_transform+0x25b>
  400ed3:	mov    0x318e(%rip),%rdx        # 404068 <meldra_bounds_checks>
  400eda:	mov    (%rax),%r8
  400edd:	cmp    $0x4,%rcx
  400ee1:	jae    400eeb <meldra_fn_transform+0x2b>
  400ee3:	xor    %r9d,%r9d
  400ee6:	jmp    4010ea <meldra_fn_transform+0x22a>
  400eeb:	movq   %rsi,%xmm0
  400ef0:	cmp    $0x10,%rcx
  400ef4:	jae    400efe <meldra_fn_transform+0x3e>
  400ef6:	xor    %r9d,%r9d
  400ef9:	jmp    401063 <meldra_fn_transform+0x1a3>
  400efe:	mov    %rcx,%r9
  400f01:	and    $0xfffffffffffffff0,%r9
  400f05:	pshufd $0x44,%xmm0,%xmm1
  400f0a:	movdqa 0x13fe(%rip),%xmm2        # 402310 <__dso_handle+0x8>
  400f12:	movdqa 0x1406(%rip),%xmm3        # 402320 <__dso_handle+0x18>
  400f1a:	movdqa 0x140e(%rip),%xmm4        # 402330 <__dso_handle+0x28>
  400f22:	movdqa 0x1416(%rip),%xmm5        # 402340 <__dso_handle+0x38>
  400f2a:	movdqa 0x141e(%rip),%xmm6        # 402350 <__dso_handle+0x48>
  400f32:	movdqa 0x1426(%rip),%xmm7        # 402360 <__dso_handle+0x58>
  400f3a:	movdqa 0x142d(%rip),%xmm8        # 402370 <__dso_handle+0x68>
  400f43:	movdqa 0x1434(%rip),%xmm9        # 402380 <__dso_handle+0x78>
  400f4c:	xor    %r10d,%r10d
  400f4f:	movdqa 0x1438(%rip),%xmm10        # 402390 <__dso_handle+0x88>
  400f58:	movdqa 0x143f(%rip),%xmm11        # 4023a0 <__dso_handle+0x98>
  400f61:	data16 data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  400f70:	movdqa %xmm2,%xmm12
  400f75:	paddq  %xmm1,%xmm12
  400f7a:	movdqa %xmm6,%xmm14
  400f7f:	paddq  %xmm1,%xmm14
  400f84:	movdqa %xmm8,%xmm15
  400f89:	paddq  %xmm1,%xmm15
  400f8e:	movdqa %xmm9,%xmm13
  400f93:	paddq  %xmm1,%xmm13
  400f98:	pand   %xmm10,%xmm13
  400f9d:	pand   %xmm10,%xmm15
  400fa2:	packuswb %xmm15,%xmm13
  400fa7:	movdqa %xmm7,%xmm15
  400fac:	paddq  %xmm1,%xmm15
  400fb1:	pand   %xmm10,%xmm15
  400fb6:	pand   %xmm10,%xmm14
  400fbb:	packuswb %xmm14,%xmm15
  400fc0:	movdqa %xmm4,%xmm14
  400fc5:	paddq  %xmm1,%xmm14
  400fca:	packuswb %xmm15,%xmm13
  400fcf:	movdqa %xmm5,%xmm15
  400fd4:	paddq  %xmm1,%xmm15
  400fd9:	pand   %xmm10,%xmm15
  400fde:	pand   %xmm10,%xmm14
  400fe3:	packuswb %xmm14,%xmm15
  400fe8:	movdqa %xmm3,%xmm14
  400fed:	paddq  %xmm1,%xmm14
  400ff2:	pand   %xmm10,%xmm14
  400ff7:	pand   %xmm10,%xmm12
  400ffc:	packuswb %xmm12,%xmm14
  401001:	packuswb %xmm14,%xmm15
  401006:	packuswb %xmm15,%xmm13
  40100b:	movdqu (%r8,%r10,1),%xmm12
  401011:	pxor   %xmm12,%xmm13
  401016:	movdqu %xmm13,(%r8,%r10,1)
  40101c:	add    $0x10,%r10
  401020:	paddq  %xmm11,%xmm9
  401025:	paddq  %xmm11,%xmm8
  40102a:	paddq  %xmm11,%xmm7
  40102f:	paddq  %xmm11,%xmm6
  401034:	paddq  %xmm11,%xmm5
  401039:	paddq  %xmm11,%xmm4
  40103e:	paddq  %xmm11,%xmm3
  401043:	paddq  %xmm11,%xmm2
  401048:	cmp    %r10,%r9
  40104b:	jne    400f70 <meldra_fn_transform+0xb0>
  401051:	cmp    %r9,%rcx
  401054:	je     401110 <meldra_fn_transform+0x250>
  40105a:	test   $0xc,%cl
  40105d:	je     4010ea <meldra_fn_transform+0x22a>
  401063:	mov    %r9,%r10
  401066:	mov    %rcx,%r9
  401069:	and    $0xfffffffffffffffc,%r9
  40106d:	pshufd $0x44,%xmm0,%xmm0
  401072:	movq   %r10,%xmm1
  401077:	pshufd $0x44,%xmm1,%xmm1
  40107c:	movdqa 0x12ec(%rip),%xmm2        # 402370 <__dso_handle+0x68>
  401084:	por    %xmm1,%xmm2
  401088:	por    0x12f0(%rip),%xmm1        # 402380 <__dso_handle+0x78>
  401090:	movdqa 0x12f8(%rip),%xmm3        # 402390 <__dso_handle+0x88>
  401098:	movdqa 0x1320(%rip),%xmm4        # 4023c0 <__dso_handle+0xb8>
  4010a0:	movd   (%r8,%r10,1),%xmm5
  4010a6:	movdqa %xmm2,%xmm6
  4010aa:	paddq  %xmm0,%xmm6
  4010ae:	movdqa %xmm1,%xmm7
  4010b2:	paddq  %xmm0,%xmm7
  4010b6:	pand   %xmm3,%xmm7
  4010ba:	pand   %xmm3,%xmm6
  4010be:	packuswb %xmm6,%xmm7
  4010c2:	packuswb %xmm7,%xmm7
  4010c6:	packuswb %xmm7,%xmm7
  4010ca:	pxor   %xmm5,%xmm7
  4010ce:	movd   %xmm7,(%r8,%r10,1)
  4010d4:	add    $0x4,%r10
  4010d8:	paddq  %xmm4,%xmm1
  4010dc:	paddq  %xmm4,%xmm2
  4010e0:	cmp    %r10,%r9
  4010e3:	jne    4010a0 <meldra_fn_transform+0x1e0>
  4010e5:	cmp    %r9,%rcx
  4010e8:	je     401110 <meldra_fn_transform+0x250>
  4010ea:	mov    %rcx,%r10
  4010ed:	sub    %r9,%r10
  4010f0:	add    %r9,%rsi
  4010f3:	add    %r9,%r8
  4010f6:	xor    %r9d,%r9d
  4010f9:	nopl   0x0(%rax)
  401100:	lea    (%rsi,%r9,1),%r11d
  401104:	xor    %r11b,(%r8,%r9,1)
  401108:	inc    %r9
  40110b:	cmp    %r9,%r10
  40110e:	jne    401100 <meldra_fn_transform+0x240>
  401110:	lea    (%rdx,%rcx,2),%rcx
  401114:	mov    %rcx,0x2f4d(%rip)        # 404068 <meldra_bounds_checks>
  40111b:	movups (%rax),%xmm0
  40111e:	movups 0x10(%rax),%xmm1
  401122:	movups %xmm1,0x10(%rdi)
  401126:	movups %xmm0,(%rdi)
  401129:	ret

Disassembly of section .fini:

000000000040112c <_fini>:
  40112c:	endbr64
  401130:	sub    $0x8,%rsp
  401134:	add    $0x8,%rsp
  401138:	ret
