
tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow/abi/meldra-optimized/program:     file format elf64-x86-64


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
  40038b:	mov    $0x401536,%esi
  400390:	xor    %eax,%eax
  400392:	call   400350 <fprintf@plt>
  400397:	call   400320 <abort@plt>
  40039c:	nopl   0x0(%rax)

00000000004003a0 <meldra_panic_alloc>:
  4003a0:	push   %rax
  4003a1:	mov    0x2c98(%rip),%rcx        # 403040 <stderr@GLIBC_2.2.5>
  4003a8:	mov    $0x401554,%edi
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
  4003e1:	mov    $0x40156f,%esi
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
  400500:	jne    400573 <main+0x83>
  400502:	mov    0x8(%rsi),%rdi
  400506:	mov    %rsi,%r13
  400509:	xor    %esi,%esi
  40050b:	mov    $0xa,%edx
  400510:	call   400340 <strtoull@plt>
  400515:	mov    %rax,%r14
  400518:	mov    0x10(%r13),%rdi
  40051c:	xor    %esi,%esi
  40051e:	mov    $0xa,%edx
  400523:	call   400340 <strtoull@plt>
  400528:	mov    %rax,%rbx
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
  400561:	test   %r14,%r14
  400564:	js     400b06 <main+0x616>
  40056a:	jne    400599 <main+0xa9>
  40056c:	xor    %eax,%eax
  40056e:	jmp    400962 <main+0x472>
  400573:	mov    0x2ac6(%rip),%rcx        # 403040 <stderr@GLIBC_2.2.5>
  40057a:	mov    $0x4013e0,%edi
  40057f:	mov    $0x1d,%esi
  400584:	mov    $0x1,%edx
  400589:	call   400370 <fwrite@plt>
  40058e:	mov    $0x2,%r14d
  400594:	jmp    400af5 <main+0x605>
  400599:	mov    %r14,%rdi
  40059c:	call   400360 <malloc@plt>
  4005a1:	test   %rax,%rax
  4005a4:	je     400b1c <main+0x62c>
  4005aa:	incq   0x2a9f(%rip)        # 403050 <meldra_heap_allocations>
  4005b1:	add    %r14,0x2aa8(%rip)        # 403060 <meldra_allocated_bytes>
  4005b8:	mov    0x2aa9(%rip),%rcx        # 403068 <meldra_bounds_checks>
  4005bf:	cmp    $0x4,%r14
  4005c3:	jae    4005cc <main+0xdc>
  4005c5:	xor    %edx,%edx
  4005c7:	jmp    400923 <main+0x433>
  4005cc:	movabs $0x7ffffffffffffff0,%rsi
  4005d6:	movq   %rbx,%xmm2
  4005db:	cmp    $0x10,%r14
  4005df:	jae    4005e8 <main+0xf8>
  4005e1:	xor    %edx,%edx
  4005e3:	jmp    400856 <main+0x366>
  4005e8:	mov    %r14,%rdx
  4005eb:	and    %rsi,%rdx
  4005ee:	movdqa %xmm2,0x20(%rsp)
  4005f4:	pshufd $0x44,%xmm2,%xmm0
  4005f9:	movdqa %xmm0,0x30(%rsp)
  4005ff:	movdqa 0xd08(%rip),%xmm14        # 401310 <__dso_handle+0x8>
  400608:	movdqa 0xd10(%rip),%xmm2        # 401320 <__dso_handle+0x18>
  400610:	movdqa 0xd18(%rip),%xmm3        # 401330 <__dso_handle+0x28>
  400618:	movdqa 0xd1f(%rip),%xmm12        # 401340 <__dso_handle+0x38>
  400621:	movdqa 0xd27(%rip),%xmm6        # 401350 <__dso_handle+0x48>
  400629:	movdqa 0xd2f(%rip),%xmm1        # 401360 <__dso_handle+0x58>
  400631:	movdqa 0xd36(%rip),%xmm11        # 401370 <__dso_handle+0x68>
  40063a:	movdqa 0xd3d(%rip),%xmm9        # 401380 <__dso_handle+0x78>
  400643:	xor    %edi,%edi
  400645:	data16 cs nopw 0x0(%rax,%rax,1)
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
  4007fe:	movdqu %xmm13,(%rax,%rdi,1)
  400804:	add    $0x10,%rdi
  400808:	movdqa 0xba0(%rip),%xmm0        # 4013b0 <__dso_handle+0xa8>
  400810:	paddq  %xmm0,%xmm9
  400815:	paddq  %xmm0,%xmm11
  40081a:	paddq  %xmm0,%xmm1
  40081e:	paddq  %xmm0,%xmm6
  400822:	paddq  %xmm0,%xmm12
  400827:	paddq  %xmm0,%xmm3
  40082b:	paddq  %xmm0,%xmm2
  40082f:	paddq  %xmm0,%xmm14
  400834:	cmp    %rdi,%rdx
  400837:	jne    400650 <main+0x160>
  40083d:	cmp    %rdx,%r14
  400840:	movdqa 0x20(%rsp),%xmm2
  400846:	je     400958 <main+0x468>
  40084c:	test   $0xc,%r14b
  400850:	je     400923 <main+0x433>
  400856:	mov    %rdx,%rdi
  400859:	add    $0xc,%rsi
  40085d:	mov    %rsi,%rdx
  400860:	and    %r14,%rdx
  400863:	movq   %rdi,%xmm0
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
  400907:	movd   %xmm10,(%rax,%rdi,1)
  40090d:	add    $0x4,%rdi
  400911:	paddq  %xmm5,%xmm0
  400915:	paddq  %xmm5,%xmm1
  400919:	cmp    %rdi,%rdx
  40091c:	jne    4008a0 <main+0x3b0>
  40091e:	cmp    %rdx,%r14
  400921:	je     400958 <main+0x468>
  400923:	mov    %edx,%edi
  400925:	shl    $0x4,%edi
  400928:	add    %edx,%edi
  40092a:	mov    %ebx,%esi
  40092c:	add    %dil,%sil
  40092f:	add    $0x17,%sil
  400933:	data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  400940:	mov    %edx,%edi
  400942:	shr    $0x3,%edi
  400945:	add    %sil,%dil
  400948:	mov    %dil,(%rax,%rdx,1)
  40094c:	inc    %rdx
  40094f:	add    $0x11,%sil
  400953:	cmp    %rdx,%r14
  400956:	jne    400940 <main+0x450>
  400958:	add    %r14,%rcx
  40095b:	mov    %rcx,0x2706(%rip)        # 403068 <meldra_bounds_checks>
  400962:	mov    %r14,%rcx
  400965:	sub    %r15,%rcx
  400968:	jb     400b0e <main+0x61e>
  40096e:	cmp    %rcx,%r12
  400971:	ja     400b0e <main+0x61e>
  400977:	add    %rax,%r15
  40097a:	test   %rax,%rax
  40097d:	cmove  %rax,%r15
  400981:	test   %r12,%r12
  400984:	je     400a55 <main+0x565>
  40098a:	mov    0x26d7(%rip),%rcx        # 403068 <meldra_bounds_checks>
  400991:	movabs $0x100000001b3,%rdx
  40099b:	mov    %r12d,%esi
  40099e:	and    $0x3,%esi
  4009a1:	cmp    $0x4,%r12
  4009a5:	jae    4009ab <main+0x4bb>
  4009a7:	xor    %edi,%edi
  4009a9:	jmp    400a1e <main+0x52e>
  4009ab:	mov    %r12,%r8
  4009ae:	and    $0xfffffffffffffffc,%r8
  4009b2:	xor    %edi,%edi
  4009b4:	data16 data16 cs nopw 0x0(%rax,%rax,1)
  4009c0:	movzbl (%r15,%rdi,1),%r9d
  4009c5:	add    %rdi,%r9
  4009c8:	add    $0x17,%r9
  4009cc:	xor    %rbx,%r9
  4009cf:	imul   %rdx,%r9
  4009d3:	movzbl 0x1(%r15,%rdi,1),%r10d
  4009d9:	add    %rdi,%r10
  4009dc:	add    $0x18,%r10
  4009e0:	xor    %r9,%r10
  4009e3:	imul   %rdx,%r10
  4009e7:	movzbl 0x2(%r15,%rdi,1),%r9d
  4009ed:	add    %rdi,%r9
  4009f0:	add    $0x19,%r9
  4009f4:	xor    %r10,%r9
  4009f7:	imul   %rdx,%r9
  4009fb:	movzbl 0x3(%r15,%rdi,1),%r10d
  400a01:	lea    (%rdi,%r10,1),%rbx
  400a05:	add    $0x1a,%rbx
  400a09:	xor    %r9,%rbx
  400a0c:	imul   %rdx,%rbx
  400a10:	add    $0x4,%rdi
  400a14:	cmp    %rdi,%r8
  400a17:	jne    4009c0 <main+0x4d0>
  400a19:	test   %rsi,%rsi
  400a1c:	je     400a4b <main+0x55b>
  400a1e:	add    $0x17,%rdi
  400a22:	mov    %rbx,%r8
  400a25:	data16 cs nopw 0x0(%rax,%rax,1)
  400a30:	movzbl -0x17(%r15,%rdi,1),%ebx
  400a36:	add    %rdi,%rbx
  400a39:	xor    %r8,%rbx
  400a3c:	imul   %rdx,%rbx
  400a40:	inc    %rdi
  400a43:	mov    %rbx,%r8
  400a46:	dec    %rsi
  400a49:	jne    400a30 <main+0x540>
  400a4b:	add    %r12,%rcx
  400a4e:	mov    %rcx,0x2613(%rip)        # 403068 <meldra_bounds_checks>
  400a55:	test   %rax,%rax
  400a58:	je     400a69 <main+0x579>
  400a5a:	mov    %rax,%rdi
  400a5d:	call   400310 <free@plt>
  400a62:	incq   0x25ef(%rip)        # 403058 <meldra_heap_frees>
  400a69:	add    %r14,%rbx
  400a6c:	mov    0x25cd(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400a73:	mov    0x25d6(%rip),%rdx        # 403050 <meldra_heap_allocations>
  400a7a:	xor    %r14d,%r14d
  400a7d:	mov    $0x4013fe,%esi
  400a82:	xor    %eax,%eax
  400a84:	call   400350 <fprintf@plt>
  400a89:	mov    0x25b0(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400a90:	mov    0x25c1(%rip),%rdx        # 403058 <meldra_heap_frees>
  400a97:	mov    0x25c2(%rip),%rcx        # 403060 <meldra_allocated_bytes>
  400a9e:	mov    0x25c3(%rip),%r9        # 403068 <meldra_bounds_checks>
  400aa5:	mov    $0x401416,%esi
  400aaa:	xor    %r8d,%r8d
  400aad:	xor    %eax,%eax
  400aaf:	call   400350 <fprintf@plt>
  400ab4:	mov    0x2585(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400abb:	mov    $0x401476,%esi
  400ac0:	xor    %edx,%edx
  400ac2:	xor    %ecx,%ecx
  400ac4:	xor    %r8d,%r8d
  400ac7:	xor    %r9d,%r9d
  400aca:	xor    %eax,%eax
  400acc:	call   400350 <fprintf@plt>
  400ad1:	mov    0x2568(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400ad8:	mov    $0x401507,%esi
  400add:	xor    %edx,%edx
  400adf:	xor    %eax,%eax
  400ae1:	call   400350 <fprintf@plt>
  400ae6:	mov    $0x40154f,%edi
  400aeb:	mov    %rbx,%rsi
  400aee:	xor    %eax,%eax
  400af0:	call   400330 <printf@plt>
  400af5:	mov    %r14d,%eax
  400af8:	add    $0x40,%rsp
  400afc:	pop    %rbx
  400afd:	pop    %r12
  400aff:	pop    %r13
  400b01:	pop    %r14
  400b03:	pop    %r15
  400b05:	ret
  400b06:	mov    %r14,%rdi
  400b09:	call   400380 <meldra_panic_bytes_allocation_overflow>
  400b0e:	mov    %r15,%rdi
  400b11:	mov    %r12,%rsi
  400b14:	mov    %r14,%rdx
  400b17:	call   4003d0 <meldra_panic_bytes_slice>
  400b1c:	call   4003a0 <meldra_panic_alloc>

Disassembly of section .fini:

0000000000400b24 <_fini>:
  400b24:	endbr64
  400b28:	sub    $0x8,%rsp
  400b2c:	add    $0x8,%rsp
  400b30:	ret
