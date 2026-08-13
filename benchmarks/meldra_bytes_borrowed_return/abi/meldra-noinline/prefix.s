
benchmarks/meldra_bytes_borrowed_return/abi/meldra-noinline/program:     file format elf64-x86-64


Disassembly of section .init:

00000000004002e0 <_init>:
  4002e0:	endbr64
  4002e4:	sub    $0x8,%rsp
  4002e8:	mov    0x2cf1(%rip),%rax        # 402fe0 <__gmon_start__>
  4002ef:	test   %rax,%rax
  4002f2:	je     4002f6 <_init+0x16>
  4002f4:	call   *%rax
  4002f6:	add    $0x8,%rsp
  4002fa:	ret

Disassembly of section .plt:

0000000000400300 <free@plt-0x10>:
  400300:	push   0x2cea(%rip)        # 402ff0 <_GLOBAL_OFFSET_TABLE_+0x8>
  400306:	jmp    *0x2cec(%rip)        # 402ff8 <_GLOBAL_OFFSET_TABLE_+0x10>
  40030c:	nopl   0x0(%rax)

0000000000400310 <free@plt>:
  400310:	jmp    *0x2cea(%rip)        # 403000 <free@GLIBC_2.2.5>
  400316:	push   $0x0
  40031b:	jmp    400300 <_init+0x20>

0000000000400320 <abort@plt>:
  400320:	jmp    *0x2ce2(%rip)        # 403008 <abort@GLIBC_2.2.5>
  400326:	push   $0x1
  40032b:	jmp    400300 <_init+0x20>

0000000000400330 <printf@plt>:
  400330:	jmp    *0x2cda(%rip)        # 403010 <printf@GLIBC_2.2.5>
  400336:	push   $0x2
  40033b:	jmp    400300 <_init+0x20>

0000000000400340 <strtoull@plt>:
  400340:	jmp    *0x2cd2(%rip)        # 403018 <strtoull@GLIBC_2.2.5>
  400346:	push   $0x3
  40034b:	jmp    400300 <_init+0x20>

0000000000400350 <fprintf@plt>:
  400350:	jmp    *0x2cca(%rip)        # 403020 <fprintf@GLIBC_2.2.5>
  400356:	push   $0x4
  40035b:	jmp    400300 <_init+0x20>

0000000000400360 <malloc@plt>:
  400360:	jmp    *0x2cc2(%rip)        # 403028 <malloc@GLIBC_2.2.5>
  400366:	push   $0x5
  40036b:	jmp    400300 <_init+0x20>

0000000000400370 <fwrite@plt>:
  400370:	jmp    *0x2cba(%rip)        # 403030 <fwrite@GLIBC_2.2.5>
  400376:	push   $0x6
  40037b:	jmp    400300 <_init+0x20>

Disassembly of section .text:

0000000000400380 <meldra_panic_bytes_allocation_overflow>:
  400380:	push   %rax
  400381:	mov    %rdi,%rdx
  400384:	mov    0x2cb5(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  40038b:	mov    $0x401476,%esi
  400390:	xor    %eax,%eax
  400392:	call   400350 <fprintf@plt>
  400397:	call   400320 <abort@plt>
  40039c:	nopl   0x0(%rax)

00000000004003a0 <meldra_panic_alloc>:
  4003a0:	push   %rax
  4003a1:	mov    0x2c98(%rip),%rcx        # 403040 <stderr@GLIBC_2.2.5>
  4003a8:	mov    $0x401494,%edi
  4003ad:	mov    $0x1a,%esi
  4003b2:	mov    $0x1,%edx
  4003b7:	call   400370 <fwrite@plt>
  4003bc:	call   400320 <abort@plt>
  4003c1:	data16 data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)

00000000004003d0 <meldra_panic_bytes_slice>:
  4003d0:	push   %rax
  4003d1:	mov    %rdx,%r8
  4003d4:	mov    %rsi,%rcx
  4003d7:	mov    %rdi,%rdx
  4003da:	mov    0x2c5f(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  4003e1:	mov    $0x4014af,%esi
  4003e6:	xor    %eax,%eax
  4003e8:	call   400350 <fprintf@plt>
  4003ed:	call   400320 <abort@plt>
  4003f2:	cs nopw 0x0(%rax,%rax,1)
  4003fc:	nopl   0x0(%rax)

0000000000400400 <_start>:
  400400:	endbr64
  400404:	xor    %ebp,%ebp
  400406:	mov    %rdx,%r9
  400409:	pop    %rsi
  40040a:	mov    %rsp,%rdx
  40040d:	and    $0xfffffffffffffff0,%rsp
  400411:	push   %rax
  400412:	push   %rsp
  400413:	xor    %r8d,%r8d
  400416:	xor    %ecx,%ecx
  400418:	mov    $0x4004f0,%rdi
  40041f:	call   *0x2bb3(%rip)        # 402fd8 <__libc_start_main@GLIBC_2.34>
  400425:	hlt
  400426:	cs nopw 0x0(%rax,%rax,1)

0000000000400430 <_dl_relocate_static_pie>:
  400430:	endbr64
  400434:	ret
  400435:	cs nopw 0x0(%rax,%rax,1)
  40043f:	nop

0000000000400440 <deregister_tm_clones>:
  400440:	mov    $0x403040,%eax
  400445:	cmp    $0x403040,%rax
  40044b:	je     400460 <deregister_tm_clones+0x20>
  40044d:	mov    $0x0,%eax
  400452:	test   %rax,%rax
  400455:	je     400460 <deregister_tm_clones+0x20>
  400457:	mov    $0x403040,%edi
  40045c:	jmp    *%rax
  40045e:	xchg   %ax,%ax
  400460:	ret
  400461:	nopl   0x0(%rax)
  400465:	data16 cs nopw 0x0(%rax,%rax,1)

0000000000400470 <register_tm_clones>:
  400470:	mov    $0x403040,%esi
  400475:	sub    $0x403040,%rsi
  40047c:	mov    %rsi,%rax
  40047f:	shr    $0x3f,%rsi
  400483:	sar    $0x3,%rax
  400487:	add    %rax,%rsi
  40048a:	sar    $1,%rsi
  40048d:	je     4004a0 <register_tm_clones+0x30>
  40048f:	mov    $0x0,%eax
  400494:	test   %rax,%rax
  400497:	je     4004a0 <register_tm_clones+0x30>
  400499:	mov    $0x403040,%edi
  40049e:	jmp    *%rax
  4004a0:	ret
  4004a1:	nopl   0x0(%rax)
  4004a5:	data16 cs nopw 0x0(%rax,%rax,1)

00000000004004b0 <__do_global_dtors_aux>:
  4004b0:	endbr64
  4004b4:	cmpb   $0x0,0x2b8d(%rip)        # 403048 <completed.0>
  4004bb:	jne    4004d0 <__do_global_dtors_aux+0x20>
  4004bd:	push   %rbp
  4004be:	mov    %rsp,%rbp
  4004c1:	call   400440 <deregister_tm_clones>
  4004c6:	movb   $0x1,0x2b7b(%rip)        # 403048 <completed.0>
  4004cd:	pop    %rbp
  4004ce:	ret
  4004cf:	nop
  4004d0:	ret
  4004d1:	nopl   0x0(%rax)
  4004d5:	data16 cs nopw 0x0(%rax,%rax,1)

00000000004004e0 <frame_dummy>:
  4004e0:	endbr64
  4004e4:	jmp    400470 <register_tm_clones>
  4004e6:	cs nopw 0x0(%rax,%rax,1)

00000000004004f0 <main>:
  4004f0:	push   %rbp
  4004f1:	push   %r15
  4004f3:	push   %r14
  4004f5:	push   %r13
  4004f7:	push   %r12
  4004f9:	push   %rbx
  4004fa:	sub    $0x58,%rsp
  4004fe:	cmp    $0x7,%edi
  400501:	jne    400591 <main+0xa1>
  400507:	mov    0x8(%rsi),%rdi
  40050b:	mov    %rsi,%r14
  40050e:	xor    %esi,%esi
  400510:	mov    $0xa,%edx
  400515:	call   400340 <strtoull@plt>
  40051a:	mov    %rax,%r12
  40051d:	mov    %rax,%r15
  400520:	mov    0x10(%r14),%rdi
  400524:	xor    %esi,%esi
  400526:	mov    $0xa,%edx
  40052b:	call   400340 <strtoull@plt>
  400530:	mov    %rax,%rbx
  400533:	mov    0x18(%r14),%rdi
  400537:	xor    %esi,%esi
  400539:	mov    $0xa,%edx
  40053e:	call   400340 <strtoull@plt>
  400543:	mov    %rax,%r13
  400546:	mov    0x20(%r14),%rdi
  40054a:	xor    %esi,%esi
  40054c:	mov    $0xa,%edx
  400551:	call   400340 <strtoull@plt>
  400556:	mov    %rax,%rbp
  400559:	mov    0x28(%r14),%rdi
  40055d:	xor    %esi,%esi
  40055f:	mov    $0xa,%edx
  400564:	call   400340 <strtoull@plt>
  400569:	mov    %rax,0x28(%rsp)
  40056e:	mov    0x30(%r14),%rdi
  400572:	xor    %esi,%esi
  400574:	mov    $0xa,%edx
  400579:	call   400340 <strtoull@plt>
  40057e:	test   %r12,%r12
  400581:	js     400b02 <main+0x612>
  400587:	jne    4005b7 <main+0xc7>
  400589:	xor    %r14d,%r14d
  40058c:	jmp    400982 <main+0x492>
  400591:	mov    0x2aa8(%rip),%rcx        # 403040 <stderr@GLIBC_2.2.5>
  400598:	mov    $0x4013e0,%edi
  40059d:	mov    $0x1d,%esi
  4005a2:	mov    $0x1,%edx
  4005a7:	call   400370 <fwrite@plt>
  4005ac:	mov    $0x2,%r14d
  4005b2:	jmp    400af0 <main+0x600>
  4005b7:	mov    %r15,%rdi
  4005ba:	call   400360 <malloc@plt>
  4005bf:	test   %rax,%rax
  4005c2:	je     400b18 <main+0x628>
  4005c8:	mov    %rax,%r14
  4005cb:	incq   0x2a7e(%rip)        # 403050 <meldra_heap_allocations>
  4005d2:	add    %r15,0x2a87(%rip)        # 403060 <meldra_allocated_bytes>
  4005d9:	mov    0x2a88(%rip),%rax        # 403068 <meldra_bounds_checks>
  4005e0:	cmp    $0x4,%r12
  4005e4:	jae    4005ed <main+0xfd>
  4005e6:	xor    %ecx,%ecx
  4005e8:	jmp    400943 <main+0x453>
  4005ed:	movabs $0x7ffffffffffffff0,%rdi
  4005f7:	movq   %rbx,%xmm2
  4005fc:	cmp    $0x10,%r12
  400600:	jae    400609 <main+0x119>
  400602:	xor    %ecx,%ecx
  400604:	jmp    400876 <main+0x386>
  400609:	mov    %r12,%rcx
  40060c:	and    %rdi,%rcx
  40060f:	movdqa %xmm2,0x30(%rsp)
  400615:	pshufd $0x44,%xmm2,%xmm0
  40061a:	movdqa %xmm0,0x40(%rsp)
  400620:	movdqa 0xce7(%rip),%xmm14        # 401310 <__dso_handle+0x8>
  400629:	movdqa 0xcef(%rip),%xmm2        # 401320 <__dso_handle+0x18>
  400631:	movdqa 0xcf7(%rip),%xmm3        # 401330 <__dso_handle+0x28>
  400639:	movdqa 0xcfe(%rip),%xmm12        # 401340 <__dso_handle+0x38>
  400642:	movdqa 0xd06(%rip),%xmm6        # 401350 <__dso_handle+0x48>
  40064a:	movdqa 0xd0e(%rip),%xmm1        # 401360 <__dso_handle+0x58>
  400652:	movdqa 0xd15(%rip),%xmm11        # 401370 <__dso_handle+0x68>
  40065b:	movdqa 0xd1c(%rip),%xmm9        # 401380 <__dso_handle+0x78>
  400664:	xor    %esi,%esi
  400666:	cs nopw 0x0(%rax,%rax,1)
  400670:	movdqa %xmm3,(%rsp)
  400675:	movdqa %xmm2,0x10(%rsp)
  40067b:	movdqa %xmm9,%xmm8
  400680:	psrlq  $0x3,%xmm8
  400686:	movdqa %xmm11,%xmm7
  40068b:	psrlq  $0x3,%xmm7
  400690:	movdqa %xmm1,%xmm10
  400695:	psrlq  $0x3,%xmm10
  40069b:	movdqa %xmm6,%xmm0
  40069f:	psrlq  $0x3,%xmm0
  4006a4:	movdqa %xmm12,%xmm5
  4006a9:	psrlq  $0x3,%xmm5
  4006ae:	movdqa (%rsp),%xmm15
  4006b4:	psrlq  $0x3,%xmm15
  4006ba:	movdqa %xmm2,%xmm4
  4006be:	psrlq  $0x3,%xmm4
  4006c3:	movdqa %xmm9,%xmm2
  4006c8:	psllq  $0x4,%xmm2
  4006cd:	movdqa %xmm9,%xmm13
  4006d2:	movdqa 0x40(%rsp),%xmm3
  4006d8:	paddq  %xmm3,%xmm13
  4006dd:	paddq  %xmm2,%xmm13
  4006e2:	movdqa %xmm11,%xmm2
  4006e7:	psllq  $0x4,%xmm2
  4006ec:	paddq  %xmm8,%xmm13
  4006f1:	movdqa %xmm11,%xmm8
  4006f6:	paddq  %xmm3,%xmm8
  4006fb:	paddq  %xmm2,%xmm8
  400700:	movdqa %xmm1,%xmm2
  400704:	psllq  $0x4,%xmm2
  400709:	paddq  %xmm7,%xmm8
  40070e:	movdqa %xmm1,%xmm7
  400712:	paddq  %xmm3,%xmm7
  400716:	paddq  %xmm2,%xmm7
  40071a:	movdqa %xmm6,%xmm2
  40071e:	psllq  $0x4,%xmm2
  400723:	paddq  %xmm10,%xmm7
  400728:	movdqa %xmm6,%xmm10
  40072d:	paddq  %xmm3,%xmm10
  400732:	paddq  %xmm2,%xmm10
  400737:	movdqa %xmm12,%xmm2
  40073c:	psllq  $0x4,%xmm2
  400741:	paddq  %xmm0,%xmm10
  400746:	movdqa %xmm12,%xmm0
  40074b:	paddq  %xmm3,%xmm0
  40074f:	paddq  %xmm2,%xmm0
  400753:	movdqa (%rsp),%xmm2
  400758:	psllq  $0x4,%xmm2
  40075d:	paddq  %xmm5,%xmm0
  400761:	movdqa (%rsp),%xmm5
  400766:	paddq  %xmm3,%xmm5
  40076a:	paddq  %xmm2,%xmm5
  40076e:	movdqa 0x10(%rsp),%xmm2
  400774:	psllq  $0x4,%xmm2
  400779:	paddq  %xmm15,%xmm5
  40077e:	movdqa 0x10(%rsp),%xmm15
  400785:	paddq  %xmm3,%xmm15
  40078a:	paddq  %xmm2,%xmm15
  40078f:	movdqa %xmm14,%xmm2
  400794:	psrlq  $0x3,%xmm2
  400799:	paddq  %xmm4,%xmm15
  40079e:	movdqa %xmm14,%xmm4
  4007a3:	psllq  $0x4,%xmm14
  4007a9:	paddq  %xmm4,%xmm14
  4007ae:	paddq  %xmm3,%xmm2
  4007b2:	paddq  %xmm14,%xmm2
  4007b7:	movdqa %xmm4,%xmm14
  4007bc:	movdqa 0xbcc(%rip),%xmm3        # 401390 <__dso_handle+0x88>
  4007c4:	pand   %xmm3,%xmm13
  4007c9:	pand   %xmm3,%xmm8
  4007ce:	packuswb %xmm8,%xmm13
  4007d3:	pand   %xmm3,%xmm7
  4007d7:	pand   %xmm3,%xmm10
  4007dc:	packuswb %xmm10,%xmm7
  4007e1:	packuswb %xmm7,%xmm13
  4007e6:	pand   %xmm3,%xmm0
  4007ea:	pand   %xmm3,%xmm5
  4007ee:	packuswb %xmm5,%xmm0
  4007f2:	pand   %xmm3,%xmm15
  4007f7:	pand   %xmm3,%xmm2
  4007fb:	packuswb %xmm2,%xmm15
  400800:	movdqa 0x10(%rsp),%xmm2
  400806:	packuswb %xmm15,%xmm0
  40080b:	movdqa (%rsp),%xmm3
  400810:	packuswb %xmm0,%xmm13
  400815:	paddb  0xb82(%rip),%xmm13        # 4013a0 <__dso_handle+0x98>
  40081e:	movdqu %xmm13,(%r14,%rsi,1)
  400824:	add    $0x10,%rsi
  400828:	movdqa 0xb80(%rip),%xmm0        # 4013b0 <__dso_handle+0xa8>
  400830:	paddq  %xmm0,%xmm9
  400835:	paddq  %xmm0,%xmm11
  40083a:	paddq  %xmm0,%xmm1
  40083e:	paddq  %xmm0,%xmm6
  400842:	paddq  %xmm0,%xmm12
  400847:	paddq  %xmm0,%xmm3
  40084b:	paddq  %xmm0,%xmm2
  40084f:	paddq  %xmm0,%xmm14
  400854:	cmp    %rsi,%rcx
  400857:	jne    400670 <main+0x180>
  40085d:	cmp    %rcx,%r12
  400860:	movdqa 0x30(%rsp),%xmm2
  400866:	je     400978 <main+0x488>
  40086c:	test   $0xc,%r12b
  400870:	je     400943 <main+0x453>
  400876:	mov    %rcx,%rsi
  400879:	add    $0xc,%rdi
  40087d:	mov    %rdi,%rcx
  400880:	and    %r12,%rcx
  400883:	movq   %rsi,%xmm0
  400888:	pshufd $0x44,%xmm0,%xmm0
  40088d:	movdqa 0xadb(%rip),%xmm1        # 401370 <__dso_handle+0x68>
  400895:	por    %xmm0,%xmm1
  400899:	por    0xadf(%rip),%xmm0        # 401380 <__dso_handle+0x78>
  4008a1:	pshufd $0x44,%xmm2,%xmm2
  4008a6:	movdqa 0xae2(%rip),%xmm3        # 401390 <__dso_handle+0x88>
  4008ae:	movdqa 0xb0a(%rip),%xmm4        # 4013c0 <__dso_handle+0xb8>
  4008b6:	movdqa 0xb12(%rip),%xmm5        # 4013d0 <__dso_handle+0xc8>
  4008be:	xchg   %ax,%ax
  4008c0:	movdqa %xmm0,%xmm6
  4008c4:	psrlq  $0x3,%xmm6
  4008c9:	movdqa %xmm1,%xmm7
  4008cd:	psrlq  $0x3,%xmm7
  4008d2:	movdqa %xmm1,%xmm8
  4008d7:	psllq  $0x4,%xmm8
  4008dd:	paddq  %xmm1,%xmm8
  4008e2:	movdqa %xmm0,%xmm9
  4008e7:	psllq  $0x4,%xmm9
  4008ed:	movdqa %xmm0,%xmm10
  4008f2:	paddq  %xmm2,%xmm10
  4008f7:	paddq  %xmm9,%xmm10
  4008fc:	paddq  %xmm6,%xmm10
  400901:	paddq  %xmm2,%xmm7
  400905:	paddq  %xmm8,%xmm7
  40090a:	pand   %xmm3,%xmm10
  40090f:	pand   %xmm3,%xmm7
  400913:	packuswb %xmm7,%xmm10
  400918:	packuswb %xmm10,%xmm10
  40091d:	packuswb %xmm10,%xmm10
  400922:	paddb  %xmm4,%xmm10
  400927:	movd   %xmm10,(%r14,%rsi,1)
  40092d:	add    $0x4,%rsi
  400931:	paddq  %xmm5,%xmm0
  400935:	paddq  %xmm5,%xmm1
  400939:	cmp    %rsi,%rcx
  40093c:	jne    4008c0 <main+0x3d0>
  40093e:	cmp    %rcx,%r12
  400941:	je     400978 <main+0x488>
  400943:	mov    %ecx,%esi
  400945:	shl    $0x4,%esi
  400948:	add    %ecx,%esi
  40094a:	mov    %ebx,%edi
  40094c:	add    %sil,%dil
  40094f:	add    $0xac,%dil
  400953:	data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  400960:	mov    %ecx,%esi
  400962:	shr    $0x3,%esi
  400965:	add    %dil,%sil
  400968:	mov    %sil,(%r14,%rcx,1)
  40096c:	inc    %rcx
  40096f:	add    $0x11,%dil
  400973:	cmp    %rcx,%r12
  400976:	jne    400960 <main+0x470>
  400978:	add    %r12,%rax
  40097b:	mov    %rax,0x26e6(%rip)        # 403068 <meldra_bounds_checks>
  400982:	sub    %r13,%r12
  400985:	jb     400b0a <main+0x61a>
  40098b:	cmp    %r12,%rbp
  40098e:	ja     400b0a <main+0x61a>
  400994:	add    %r14,%r13
  400997:	test   %r14,%r14
  40099a:	cmove  %r14,%r13
  40099e:	mov    %r13,%rdi
  4009a1:	mov    %rbp,%rsi
  4009a4:	mov    0x28(%rsp),%rdx
  4009a9:	call   400b20 <meldra_fn_chain>
  4009ae:	test   %rdx,%rdx
  4009b1:	je     400a85 <main+0x595>
  4009b7:	mov    0x26aa(%rip),%rcx        # 403068 <meldra_bounds_checks>
  4009be:	movabs $0x100000001b3,%rsi
  4009c8:	mov    %edx,%edi
  4009ca:	and    $0x3,%edi
  4009cd:	cmp    $0x4,%rdx
  4009d1:	jae    4009d8 <main+0x4e8>
  4009d3:	xor    %r8d,%r8d
  4009d6:	jmp    400a4e <main+0x55e>
  4009d8:	mov    %rdx,%r9
  4009db:	and    $0xfffffffffffffffc,%r9
  4009df:	xor    %r8d,%r8d
  4009e2:	data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  4009f0:	movzbl (%rax,%r8,1),%r10d
  4009f5:	add    %r8,%r10
  4009f8:	add    $0x1f,%r10
  4009fc:	xor    %rbx,%r10
  4009ff:	imul   %rsi,%r10
  400a03:	movzbl 0x1(%rax,%r8,1),%r11d
  400a09:	add    %r8,%r11
  400a0c:	add    $0x20,%r11
  400a10:	xor    %r10,%r11
  400a13:	imul   %rsi,%r11
  400a17:	movzbl 0x2(%rax,%r8,1),%r10d
  400a1d:	add    %r8,%r10
  400a20:	add    $0x21,%r10
  400a24:	xor    %r11,%r10
  400a27:	imul   %rsi,%r10
  400a2b:	movzbl 0x3(%rax,%r8,1),%r11d
  400a31:	lea    (%r8,%r11,1),%rbx
  400a35:	add    $0x22,%rbx
  400a39:	xor    %r10,%rbx
  400a3c:	imul   %rsi,%rbx
  400a40:	add    $0x4,%r8
  400a44:	cmp    %r8,%r9
  400a47:	jne    4009f0 <main+0x500>
  400a49:	test   %rdi,%rdi
  400a4c:	je     400a7b <main+0x58b>
  400a4e:	add    $0x1f,%r8
  400a52:	mov    %rbx,%r9
  400a55:	data16 cs nopw 0x0(%rax,%rax,1)
  400a60:	movzbl -0x1f(%rax,%r8,1),%ebx
  400a66:	add    %r8,%rbx
  400a69:	xor    %r9,%rbx
  400a6c:	imul   %rsi,%rbx
  400a70:	inc    %r8
  400a73:	mov    %rbx,%r9
  400a76:	dec    %rdi
  400a79:	jne    400a60 <main+0x570>
  400a7b:	add    %rdx,%rcx
  400a7e:	mov    %rcx,0x25e3(%rip)        # 403068 <meldra_bounds_checks>
  400a85:	test   %r14,%r14
  400a88:	je     400a99 <main+0x5a9>
  400a8a:	mov    %r14,%rdi
  400a8d:	call   400310 <free@plt>
  400a92:	incq   0x25bf(%rip)        # 403058 <meldra_heap_frees>
  400a99:	mov    0x25a0(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400aa0:	mov    0x25a9(%rip),%rdx        # 403050 <meldra_heap_allocations>
  400aa7:	xor    %r14d,%r14d
  400aaa:	mov    $0x4013fe,%esi
  400aaf:	xor    %eax,%eax
  400ab1:	call   400350 <fprintf@plt>
  400ab6:	mov    0x2583(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400abd:	mov    0x2594(%rip),%rdx        # 403058 <meldra_heap_frees>
  400ac4:	mov    0x2595(%rip),%rcx        # 403060 <meldra_allocated_bytes>
  400acb:	mov    0x2596(%rip),%r9        # 403068 <meldra_bounds_checks>
  400ad2:	mov    $0x401416,%esi
  400ad7:	xor    %r8d,%r8d
  400ada:	xor    %eax,%eax
  400adc:	call   400350 <fprintf@plt>
  400ae1:	mov    $0x40148f,%edi
  400ae6:	mov    %rbx,%rsi
  400ae9:	xor    %eax,%eax
  400aeb:	call   400330 <printf@plt>
  400af0:	mov    %r14d,%eax
  400af3:	add    $0x58,%rsp
  400af7:	pop    %rbx
  400af8:	pop    %r12
  400afa:	pop    %r13
  400afc:	pop    %r14
  400afe:	pop    %r15
  400b00:	pop    %rbp
  400b01:	ret
  400b02:	mov    %r15,%rdi
  400b05:	call   400380 <meldra_panic_bytes_allocation_overflow>
  400b0a:	mov    %r13,%rdi
  400b0d:	mov    %rbp,%rsi
  400b10:	mov    %r15,%rdx
  400b13:	call   4003d0 <meldra_panic_bytes_slice>
  400b18:	call   4003a0 <meldra_panic_alloc>
  400b1d:	nopl   (%rax)

0000000000400b20 <meldra_fn_chain>:
  400b20:	push   %r14
  400b22:	push   %rbx
  400b23:	push   %rax
  400b24:	mov    %rdx,%r14
  400b27:	mov    %rsi,%rbx
  400b2a:	mov    %rsi,%rdx
  400b2d:	call   400b70 <meldra_fn_prefix>
  400b32:	sub    %r14,%rbx
  400b35:	mov    %rdx,%rcx
  400b38:	sub    %r14,%rcx
  400b3b:	jb     400b5a <meldra_fn_chain+0x3a>
  400b3d:	cmp    %rcx,%rbx
  400b40:	ja     400b5a <meldra_fn_chain+0x3a>
  400b42:	add    %rax,%r14
  400b45:	test   %rax,%rax
  400b48:	cmove  %rax,%r14
  400b4c:	mov    %r14,%rax
  400b4f:	mov    %rbx,%rdx
  400b52:	add    $0x8,%rsp
  400b56:	pop    %rbx
  400b57:	pop    %r14
  400b59:	ret
  400b5a:	mov    %r14,%rdi
  400b5d:	mov    %rbx,%rsi
  400b60:	call   4003d0 <meldra_panic_bytes_slice>
  400b65:	data16 cs nopw 0x0(%rax,%rax,1)

0000000000400b70 <meldra_fn_prefix>:
  400b70:	mov    %rdi,%rax
  400b73:	cmp    %rsi,%rdx
  400b76:	cmovae %rsi,%rdx
  400b7a:	ret

Disassembly of section .fini:

0000000000400b7c <_fini>:
  400b7c:	endbr64
  400b80:	sub    $0x8,%rsp
  400b84:	add    $0x8,%rsp
  400b88:	ret
