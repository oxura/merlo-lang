
tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow/abi/meldra-noinline/program:     file format elf64-x86-64


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
  40038b:	mov    $0x40160b,%esi
  400390:	xor    %eax,%eax
  400392:	call   400350 <fprintf@plt>
  400397:	call   400320 <abort@plt>
  40039c:	nopl   0x0(%rax)

00000000004003a0 <meldra_panic_alloc>:
  4003a0:	push   %rax
  4003a1:	mov    0x2c98(%rip),%rcx        # 403040 <stderr@GLIBC_2.2.5>
  4003a8:	mov    $0x401629,%edi
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
  4003e1:	mov    $0x401644,%esi
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
  4004f0:	push   %r15
  4004f2:	push   %r14
  4004f4:	push   %r13
  4004f6:	push   %r12
  4004f8:	push   %rbx
  4004f9:	sub    $0x40,%rsp
  4004fd:	cmp    $0x6,%edi
  400500:	jne    400574 <main+0x84>
  400502:	mov    0x8(%rsi),%rdi
  400506:	mov    %rsi,%r13
  400509:	xor    %esi,%esi
  40050b:	mov    $0xa,%edx
  400510:	call   400340 <strtoull@plt>
  400515:	mov    %rax,%rbx
  400518:	mov    0x10(%r13),%rdi
  40051c:	xor    %esi,%esi
  40051e:	mov    $0xa,%edx
  400523:	call   400340 <strtoull@plt>
  400528:	mov    %rax,%r14
  40052b:	mov    0x18(%r13),%rdi
  40052f:	xor    %esi,%esi
  400531:	mov    $0xa,%edx
  400536:	call   400340 <strtoull@plt>
  40053b:	mov    %rax,%r15
  40053e:	mov    0x20(%r13),%rdi
  400542:	xor    %esi,%esi
  400544:	mov    $0xa,%edx
  400549:	call   400340 <strtoull@plt>
  40054e:	mov    %rax,%r12
  400551:	mov    0x28(%r13),%rdi
  400555:	xor    %esi,%esi
  400557:	mov    $0xa,%edx
  40055c:	call   400340 <strtoull@plt>
  400561:	test   %rbx,%rbx
  400564:	js     400b70 <main+0x680>
  40056a:	jne    400599 <main+0xa9>
  40056c:	xor    %r13d,%r13d
  40056f:	jmp    400962 <main+0x472>
  400574:	mov    0x2ac5(%rip),%rcx        # 403040 <stderr@GLIBC_2.2.5>
  40057b:	mov    $0x4013e0,%edi
  400580:	mov    $0x1d,%esi
  400585:	mov    $0x1,%edx
  40058a:	call   400370 <fwrite@plt>
  40058f:	mov    $0x2,%ebx
  400594:	jmp    400b60 <main+0x670>
  400599:	mov    %rbx,%rdi
  40059c:	call   400360 <malloc@plt>
  4005a1:	test   %rax,%rax
  4005a4:	je     400b86 <main+0x696>
  4005aa:	mov    %rax,%r13
  4005ad:	incq   0x2b0c(%rip)        # 4030c0 <meldra_heap_allocations>
  4005b4:	add    %rbx,0x2b15(%rip)        # 4030d0 <meldra_allocated_bytes>
  4005bb:	mov    0x2b16(%rip),%rax        # 4030d8 <meldra_bounds_checks>
  4005c2:	cmp    $0x4,%rbx
  4005c6:	jae    4005cf <main+0xdf>
  4005c8:	xor    %ecx,%ecx
  4005ca:	jmp    400924 <main+0x434>
  4005cf:	movabs $0x7ffffffffffffff0,%rdx
  4005d9:	movq   %r14,%xmm2
  4005de:	cmp    $0x10,%rbx
  4005e2:	jae    4005eb <main+0xfb>
  4005e4:	xor    %ecx,%ecx
  4005e6:	jmp    400856 <main+0x366>
  4005eb:	mov    %rbx,%rcx
  4005ee:	and    %rdx,%rcx
  4005f1:	movdqa %xmm2,0x20(%rsp)
  4005f7:	pshufd $0x44,%xmm2,%xmm0
  4005fc:	movdqa %xmm0,0x30(%rsp)
  400602:	movdqa 0xd05(%rip),%xmm14        # 401310 <__dso_handle+0x8>
  40060b:	movdqa 0xd0d(%rip),%xmm2        # 401320 <__dso_handle+0x18>
  400613:	movdqa 0xd15(%rip),%xmm3        # 401330 <__dso_handle+0x28>
  40061b:	movdqa 0xd1c(%rip),%xmm12        # 401340 <__dso_handle+0x38>
  400624:	movdqa 0xd24(%rip),%xmm6        # 401350 <__dso_handle+0x48>
  40062c:	movdqa 0xd2c(%rip),%xmm1        # 401360 <__dso_handle+0x58>
  400634:	movdqa 0xd33(%rip),%xmm11        # 401370 <__dso_handle+0x68>
  40063d:	movdqa 0xd3a(%rip),%xmm9        # 401380 <__dso_handle+0x78>
  400646:	xor    %esi,%esi
  400648:	nopl   0x0(%rax,%rax,1)
  400650:	movdqa %xmm3,(%rsp)
  400655:	movdqa %xmm2,0x10(%rsp)
  40065b:	movdqa %xmm9,%xmm8
  400660:	psrlq  $0x3,%xmm8
  400666:	movdqa %xmm11,%xmm7
  40066b:	psrlq  $0x3,%xmm7
  400670:	movdqa %xmm1,%xmm10
  400675:	psrlq  $0x3,%xmm10
  40067b:	movdqa %xmm6,%xmm0
  40067f:	psrlq  $0x3,%xmm0
  400684:	movdqa %xmm12,%xmm5
  400689:	psrlq  $0x3,%xmm5
  40068e:	movdqa (%rsp),%xmm15
  400694:	psrlq  $0x3,%xmm15
  40069a:	movdqa %xmm2,%xmm4
  40069e:	psrlq  $0x3,%xmm4
  4006a3:	movdqa %xmm9,%xmm2
  4006a8:	psllq  $0x4,%xmm2
  4006ad:	movdqa %xmm9,%xmm13
  4006b2:	movdqa 0x30(%rsp),%xmm3
  4006b8:	paddq  %xmm3,%xmm13
  4006bd:	paddq  %xmm2,%xmm13
  4006c2:	movdqa %xmm11,%xmm2
  4006c7:	psllq  $0x4,%xmm2
  4006cc:	paddq  %xmm8,%xmm13
  4006d1:	movdqa %xmm11,%xmm8
  4006d6:	paddq  %xmm3,%xmm8
  4006db:	paddq  %xmm2,%xmm8
  4006e0:	movdqa %xmm1,%xmm2
  4006e4:	psllq  $0x4,%xmm2
  4006e9:	paddq  %xmm7,%xmm8
  4006ee:	movdqa %xmm1,%xmm7
  4006f2:	paddq  %xmm3,%xmm7
  4006f6:	paddq  %xmm2,%xmm7
  4006fa:	movdqa %xmm6,%xmm2
  4006fe:	psllq  $0x4,%xmm2
  400703:	paddq  %xmm10,%xmm7
  400708:	movdqa %xmm6,%xmm10
  40070d:	paddq  %xmm3,%xmm10
  400712:	paddq  %xmm2,%xmm10
  400717:	movdqa %xmm12,%xmm2
  40071c:	psllq  $0x4,%xmm2
  400721:	paddq  %xmm0,%xmm10
  400726:	movdqa %xmm12,%xmm0
  40072b:	paddq  %xmm3,%xmm0
  40072f:	paddq  %xmm2,%xmm0
  400733:	movdqa (%rsp),%xmm2
  400738:	psllq  $0x4,%xmm2
  40073d:	paddq  %xmm5,%xmm0
  400741:	movdqa (%rsp),%xmm5
  400746:	paddq  %xmm3,%xmm5
  40074a:	paddq  %xmm2,%xmm5
  40074e:	movdqa 0x10(%rsp),%xmm2
  400754:	psllq  $0x4,%xmm2
  400759:	paddq  %xmm15,%xmm5
  40075e:	movdqa 0x10(%rsp),%xmm15
  400765:	paddq  %xmm3,%xmm15
  40076a:	paddq  %xmm2,%xmm15
  40076f:	movdqa %xmm14,%xmm2
  400774:	psrlq  $0x3,%xmm2
  400779:	paddq  %xmm4,%xmm15
  40077e:	movdqa %xmm14,%xmm4
  400783:	psllq  $0x4,%xmm14
  400789:	paddq  %xmm4,%xmm14
  40078e:	paddq  %xmm3,%xmm2
  400792:	paddq  %xmm14,%xmm2
  400797:	movdqa %xmm4,%xmm14
  40079c:	movdqa 0xbec(%rip),%xmm3        # 401390 <__dso_handle+0x88>
  4007a4:	pand   %xmm3,%xmm13
  4007a9:	pand   %xmm3,%xmm8
  4007ae:	packuswb %xmm8,%xmm13
  4007b3:	pand   %xmm3,%xmm7
  4007b7:	pand   %xmm3,%xmm10
  4007bc:	packuswb %xmm10,%xmm7
  4007c1:	packuswb %xmm7,%xmm13
  4007c6:	pand   %xmm3,%xmm0
  4007ca:	pand   %xmm3,%xmm5
  4007ce:	packuswb %xmm5,%xmm0
  4007d2:	pand   %xmm3,%xmm15
  4007d7:	pand   %xmm3,%xmm2
  4007db:	packuswb %xmm2,%xmm15
  4007e0:	movdqa 0x10(%rsp),%xmm2
  4007e6:	packuswb %xmm15,%xmm0
  4007eb:	movdqa (%rsp),%xmm3
  4007f0:	packuswb %xmm0,%xmm13
  4007f5:	paddb  0xba2(%rip),%xmm13        # 4013a0 <__dso_handle+0x98>
  4007fe:	movdqu %xmm13,0x0(%r13,%rsi,1)
  400805:	add    $0x10,%rsi
  400809:	movdqa 0xb9f(%rip),%xmm0        # 4013b0 <__dso_handle+0xa8>
  400811:	paddq  %xmm0,%xmm9
  400816:	paddq  %xmm0,%xmm11
  40081b:	paddq  %xmm0,%xmm1
  40081f:	paddq  %xmm0,%xmm6
  400823:	paddq  %xmm0,%xmm12
  400828:	paddq  %xmm0,%xmm3
  40082c:	paddq  %xmm0,%xmm2
  400830:	paddq  %xmm0,%xmm14
  400835:	cmp    %rsi,%rcx
  400838:	jne    400650 <main+0x160>
  40083e:	cmp    %rcx,%rbx
  400841:	movdqa 0x20(%rsp),%xmm2
  400847:	je     400958 <main+0x468>
  40084d:	test   $0xc,%bl
  400850:	je     400924 <main+0x434>
  400856:	mov    %rcx,%rsi
  400859:	add    $0xc,%rdx
  40085d:	mov    %rdx,%rcx
  400860:	and    %rbx,%rcx
  400863:	movq   %rsi,%xmm0
  400868:	pshufd $0x44,%xmm0,%xmm0
  40086d:	movdqa 0xafb(%rip),%xmm1        # 401370 <__dso_handle+0x68>
  400875:	por    %xmm0,%xmm1
  400879:	por    0xaff(%rip),%xmm0        # 401380 <__dso_handle+0x78>
  400881:	pshufd $0x44,%xmm2,%xmm2
  400886:	movdqa 0xb02(%rip),%xmm3        # 401390 <__dso_handle+0x88>
  40088e:	movdqa 0xb2a(%rip),%xmm4        # 4013c0 <__dso_handle+0xb8>
  400896:	movdqa 0xb32(%rip),%xmm5        # 4013d0 <__dso_handle+0xc8>
  40089e:	xchg   %ax,%ax
  4008a0:	movdqa %xmm0,%xmm6
  4008a4:	psrlq  $0x3,%xmm6
  4008a9:	movdqa %xmm1,%xmm7
  4008ad:	psrlq  $0x3,%xmm7
  4008b2:	movdqa %xmm1,%xmm8
  4008b7:	psllq  $0x4,%xmm8
  4008bd:	paddq  %xmm1,%xmm8
  4008c2:	movdqa %xmm0,%xmm9
  4008c7:	psllq  $0x4,%xmm9
  4008cd:	movdqa %xmm0,%xmm10
  4008d2:	paddq  %xmm2,%xmm10
  4008d7:	paddq  %xmm9,%xmm10
  4008dc:	paddq  %xmm6,%xmm10
  4008e1:	paddq  %xmm2,%xmm7
  4008e5:	paddq  %xmm8,%xmm7
  4008ea:	pand   %xmm3,%xmm10
  4008ef:	pand   %xmm3,%xmm7
  4008f3:	packuswb %xmm7,%xmm10
  4008f8:	packuswb %xmm10,%xmm10
  4008fd:	packuswb %xmm10,%xmm10
  400902:	paddb  %xmm4,%xmm10
  400907:	movd   %xmm10,0x0(%r13,%rsi,1)
  40090e:	add    $0x4,%rsi
  400912:	paddq  %xmm5,%xmm0
  400916:	paddq  %xmm5,%xmm1
  40091a:	cmp    %rsi,%rcx
  40091d:	jne    4008a0 <main+0x3b0>
  40091f:	cmp    %rcx,%rbx
  400922:	je     400958 <main+0x468>
  400924:	mov    %ecx,%esi
  400926:	shl    $0x4,%esi
  400929:	add    %ecx,%esi
  40092b:	mov    %r14d,%edx
  40092e:	add    %sil,%dl
  400931:	add    $0x17,%dl
  400934:	data16 data16 cs nopw 0x0(%rax,%rax,1)
  400940:	mov    %ecx,%esi
  400942:	shr    $0x3,%esi
  400945:	add    %dl,%sil
  400948:	mov    %sil,0x0(%r13,%rcx,1)
  40094d:	inc    %rcx
  400950:	add    $0x11,%dl
  400953:	cmp    %rcx,%rbx
  400956:	jne    400940 <main+0x450>
  400958:	add    %rbx,%rax
  40095b:	mov    %rax,0x2776(%rip)        # 4030d8 <meldra_bounds_checks>
  400962:	mov    %rbx,%rax
  400965:	sub    %r15,%rax
  400968:	jb     400b78 <main+0x688>
  40096e:	cmp    %rax,%r12
  400971:	ja     400b78 <main+0x688>
  400977:	add    %r13,%r15
  40097a:	test   %r13,%r13
  40097d:	cmove  %r13,%r15
  400981:	mov    %r13,0x2728(%rip)        # 4030b0 <meldra_reborrow_owner_data>
  400988:	mov    %rbx,0x2729(%rip)        # 4030b8 <meldra_reborrow_owner_length>
  40098f:	mov    %r15,0x26da(%rip)        # 403070 <meldra_reborrow_root_data>
  400996:	mov    %r12,0x26f3(%rip)        # 403090 <meldra_reborrow_root_length>
  40099d:	mov    0x271c(%rip),%rax        # 4030c0 <meldra_heap_allocations>
  4009a4:	mov    %rax,0x26ad(%rip)        # 403058 <meldra_reborrow_before_allocations>
  4009ab:	mov    0x2716(%rip),%rax        # 4030c8 <meldra_heap_frees>
  4009b2:	mov    %rax,0x26af(%rip)        # 403068 <meldra_reborrow_before_frees>
  4009b9:	mov    %r15,%rdi
  4009bc:	mov    %r12,%rsi
  4009bf:	mov    %r14,%rdx
  4009c2:	call   400b90 <meldra_fn_outer>
  4009c7:	mov    %rax,%r14
  4009ca:	mov    0x26ef(%rip),%rdx        # 4030c0 <meldra_heap_allocations>
  4009d1:	mov    %rdx,0x2678(%rip)        # 403050 <meldra_reborrow_after_allocations>
  4009d8:	mov    0x26e9(%rip),%rcx        # 4030c8 <meldra_heap_frees>
  4009df:	mov    %rcx,0x267a(%rip)        # 403060 <meldra_reborrow_after_frees>
  4009e6:	test   %r13,%r13
  4009e9:	je     400a08 <main+0x518>
  4009eb:	mov    %r13,%rdi
  4009ee:	call   400310 <free@plt>
  4009f3:	incq   0x26ce(%rip)        # 4030c8 <meldra_heap_frees>
  4009fa:	mov    0x264f(%rip),%rdx        # 403050 <meldra_reborrow_after_allocations>
  400a01:	mov    0x2658(%rip),%rcx        # 403060 <meldra_reborrow_after_frees>
  400a08:	add    %rbx,%r14
  400a0b:	mov    0x262e(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400a12:	sub    0x263f(%rip),%rdx        # 403058 <meldra_reborrow_before_allocations>
  400a19:	sub    0x2648(%rip),%rcx        # 403068 <meldra_reborrow_before_frees>
  400a20:	xor    %ebx,%ebx
  400a22:	mov    $0x4013fe,%esi
  400a27:	xor    %r8d,%r8d
  400a2a:	xor    %eax,%eax
  400a2c:	call   400350 <fprintf@plt>
  400a31:	mov    0x2638(%rip),%rax        # 403070 <meldra_reborrow_root_data>
  400a38:	mov    0x2639(%rip),%rcx        # 403078 <meldra_reborrow_outer_data>
  400a3f:	xor    %rax,%rcx
  400a42:	mov    0x2637(%rip),%rsi        # 403080 <meldra_reborrow_middle_data>
  400a49:	xor    %rax,%rsi
  400a4c:	or     %rcx,%rsi
  400a4f:	mov    0x2632(%rip),%rcx        # 403088 <meldra_reborrow_leaf_data>
  400a56:	xor    %rax,%rcx
  400a59:	xor    %edx,%edx
  400a5b:	or     %rsi,%rcx
  400a5e:	sete   %dl
  400a61:	mov    0x2628(%rip),%rsi        # 403090 <meldra_reborrow_root_length>
  400a68:	mov    0x2629(%rip),%rcx        # 403098 <meldra_reborrow_outer_length>
  400a6f:	xor    %rsi,%rcx
  400a72:	mov    0x2627(%rip),%rdi        # 4030a0 <meldra_reborrow_middle_length>
  400a79:	xor    %rsi,%rdi
  400a7c:	or     %rcx,%rdi
  400a7f:	mov    0x2622(%rip),%r8        # 4030a8 <meldra_reborrow_leaf_length>
  400a86:	xor    %rsi,%r8
  400a89:	xor    %ecx,%ecx
  400a8b:	or     %rdi,%r8
  400a8e:	sete   %cl
  400a91:	mov    0x2618(%rip),%rdi        # 4030b0 <meldra_reborrow_owner_data>
  400a98:	test   %rdi,%rdi
  400a9b:	sete   %r8b
  400a9f:	sub    %rdi,%rax
  400aa2:	setb   %dil
  400aa6:	or     %r8b,%dil
  400aa9:	mov    $0x0,%r8d
  400aaf:	jne    400ac7 <main+0x5d7>
  400ab1:	mov    0x2600(%rip),%rdi        # 4030b8 <meldra_reborrow_owner_length>
  400ab8:	sub    %rax,%rdi
  400abb:	jb     400ac7 <main+0x5d7>
  400abd:	xor    %r8d,%r8d
  400ac0:	cmp    %rdi,%rsi
  400ac3:	setbe  %r8b
  400ac7:	mov    0x2572(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400ace:	mov    $0x40146e,%esi
  400ad3:	xor    %eax,%eax
  400ad5:	call   400350 <fprintf@plt>
  400ada:	mov    0x255f(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400ae1:	mov    0x25d8(%rip),%rdx        # 4030c0 <meldra_heap_allocations>
  400ae8:	mov    $0x4014d3,%esi
  400aed:	xor    %eax,%eax
  400aef:	call   400350 <fprintf@plt>
  400af4:	mov    0x2545(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400afb:	mov    0x25c6(%rip),%rdx        # 4030c8 <meldra_heap_frees>
  400b02:	mov    0x25c7(%rip),%rcx        # 4030d0 <meldra_allocated_bytes>
  400b09:	mov    0x25c8(%rip),%r9        # 4030d8 <meldra_bounds_checks>
  400b10:	mov    $0x4014eb,%esi
  400b15:	xor    %r8d,%r8d
  400b18:	xor    %eax,%eax
  400b1a:	call   400350 <fprintf@plt>
  400b1f:	mov    0x251a(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400b26:	mov    $0x40154b,%esi
  400b2b:	xor    %edx,%edx
  400b2d:	xor    %ecx,%ecx
  400b2f:	xor    %r8d,%r8d
  400b32:	xor    %r9d,%r9d
  400b35:	xor    %eax,%eax
  400b37:	call   400350 <fprintf@plt>
  400b3c:	mov    0x24fd(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400b43:	mov    $0x4015dc,%esi
  400b48:	xor    %edx,%edx
  400b4a:	xor    %eax,%eax
  400b4c:	call   400350 <fprintf@plt>
  400b51:	mov    $0x401624,%edi
  400b56:	mov    %r14,%rsi
  400b59:	xor    %eax,%eax
  400b5b:	call   400330 <printf@plt>
  400b60:	mov    %ebx,%eax
  400b62:	add    $0x40,%rsp
  400b66:	pop    %rbx
  400b67:	pop    %r12
  400b69:	pop    %r13
  400b6b:	pop    %r14
  400b6d:	pop    %r15
  400b6f:	ret
  400b70:	mov    %rbx,%rdi
  400b73:	call   400380 <meldra_panic_bytes_allocation_overflow>
  400b78:	mov    %r15,%rdi
  400b7b:	mov    %r12,%rsi
  400b7e:	mov    %rbx,%rdx
  400b81:	call   4003d0 <meldra_panic_bytes_slice>
  400b86:	call   4003a0 <meldra_panic_alloc>
  400b8b:	nopl   0x0(%rax,%rax,1)

0000000000400b90 <meldra_fn_outer>:
  400b90:	mov    %rdi,0x24e1(%rip)        # 403078 <meldra_reborrow_outer_data>
  400b97:	mov    %rsi,0x24fa(%rip)        # 403098 <meldra_reborrow_outer_length>
  400b9e:	jmp    400ba0 <meldra_fn_middle>

0000000000400ba0 <meldra_fn_middle>:
  400ba0:	mov    %rdi,0x24d9(%rip)        # 403080 <meldra_reborrow_middle_data>
  400ba7:	mov    %rsi,0x24f2(%rip)        # 4030a0 <meldra_reborrow_middle_length>
  400bae:	jmp    400bb0 <meldra_fn_leaf>

0000000000400bb0 <meldra_fn_leaf>:
  400bb0:	mov    %rdx,%rax
  400bb3:	mov    %rdi,0x24ce(%rip)        # 403088 <meldra_reborrow_leaf_data>
  400bba:	mov    %rsi,0x24e7(%rip)        # 4030a8 <meldra_reborrow_leaf_length>
  400bc1:	test   %rsi,%rsi
  400bc4:	je     400c95 <meldra_fn_leaf+0xe5>
  400bca:	mov    0x2507(%rip),%rcx        # 4030d8 <meldra_bounds_checks>
  400bd1:	movabs $0x100000001b3,%rdx
  400bdb:	mov    %esi,%r8d
  400bde:	and    $0x3,%r8d
  400be2:	cmp    $0x4,%rsi
  400be6:	jae    400bed <meldra_fn_leaf+0x3d>
  400be8:	xor    %r9d,%r9d
  400beb:	jmp    400c5d <meldra_fn_leaf+0xad>
  400bed:	mov    %rsi,%r10
  400bf0:	and    $0xfffffffffffffffc,%r10
  400bf4:	xor    %r9d,%r9d
  400bf7:	nopw   0x0(%rax,%rax,1)
  400c00:	movzbl (%rdi,%r9,1),%r11d
  400c05:	add    %r9,%r11
  400c08:	add    $0x17,%r11
  400c0c:	xor    %rax,%r11
  400c0f:	imul   %rdx,%r11
  400c13:	movzbl 0x1(%rdi,%r9,1),%eax
  400c19:	add    %r9,%rax
  400c1c:	add    $0x18,%rax
  400c20:	xor    %r11,%rax
  400c23:	imul   %rdx,%rax
  400c27:	movzbl 0x2(%rdi,%r9,1),%r11d
  400c2d:	add    %r9,%r11
  400c30:	add    $0x19,%r11
  400c34:	xor    %rax,%r11
  400c37:	imul   %rdx,%r11
  400c3b:	movzbl 0x3(%rdi,%r9,1),%eax
  400c41:	add    %r9,%rax
  400c44:	add    $0x1a,%rax
  400c48:	xor    %r11,%rax
  400c4b:	imul   %rdx,%rax
  400c4f:	add    $0x4,%r9
  400c53:	cmp    %r9,%r10
  400c56:	jne    400c00 <meldra_fn_leaf+0x50>
  400c58:	test   %r8,%r8
  400c5b:	je     400c8b <meldra_fn_leaf+0xdb>
  400c5d:	add    $0x17,%r9
  400c61:	mov    %rax,%r10
  400c64:	data16 data16 cs nopw 0x0(%rax,%rax,1)
  400c70:	movzbl -0x17(%rdi,%r9,1),%eax
  400c76:	add    %r9,%rax
  400c79:	xor    %r10,%rax
  400c7c:	imul   %rdx,%rax
  400c80:	inc    %r9
  400c83:	mov    %rax,%r10
  400c86:	dec    %r8
  400c89:	jne    400c70 <meldra_fn_leaf+0xc0>
  400c8b:	add    %rsi,%rcx
  400c8e:	mov    %rcx,0x2443(%rip)        # 4030d8 <meldra_bounds_checks>
  400c95:	ret

Disassembly of section .fini:

0000000000400c98 <_fini>:
  400c98:	endbr64
  400c9c:	sub    $0x8,%rsp
  400ca0:	add    $0x8,%rsp
  400ca4:	ret
