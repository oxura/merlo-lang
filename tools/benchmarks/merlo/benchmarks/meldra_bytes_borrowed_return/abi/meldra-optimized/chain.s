
tools/benchmarks/merlo/benchmarks/meldra_bytes_borrowed_return/abi/meldra-optimized/program:     file format elf64-x86-64


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
  400501:	jne    400590 <main+0xa0>
  400507:	mov    %rsi,%r14
  40050a:	mov    0x8(%rsi),%rdi
  40050e:	xor    %esi,%esi
  400510:	mov    $0xa,%edx
  400515:	call   400340 <strtoull@plt>
  40051a:	mov    %rax,%r13
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
  400543:	mov    %rax,%r12
  400546:	mov    0x20(%r14),%rdi
  40054a:	xor    %esi,%esi
  40054c:	mov    $0xa,%edx
  400551:	call   400340 <strtoull@plt>
  400556:	mov    %rax,0x8(%rsp)
  40055b:	mov    0x28(%r14),%rdi
  40055f:	xor    %esi,%esi
  400561:	mov    $0xa,%edx
  400566:	call   400340 <strtoull@plt>
  40056b:	mov    %rax,%rbp
  40056e:	mov    0x30(%r14),%rdi
  400572:	xor    %esi,%esi
  400574:	mov    $0xa,%edx
  400579:	call   400340 <strtoull@plt>
  40057e:	test   %r13,%r13
  400581:	js     400b12 <main+0x622>
  400587:	jne    4005b6 <main+0xc6>
  400589:	xor    %eax,%eax
  40058b:	jmp    400992 <main+0x4a2>
  400590:	mov    0x2aa9(%rip),%rcx        # 403040 <stderr@GLIBC_2.2.5>
  400597:	mov    $0x4013e0,%edi
  40059c:	mov    $0x1d,%esi
  4005a1:	mov    $0x1,%edx
  4005a6:	call   400370 <fwrite@plt>
  4005ab:	mov    $0x2,%r14d
  4005b1:	jmp    400b00 <main+0x610>
  4005b6:	mov    %r15,%rdi
  4005b9:	call   400360 <malloc@plt>
  4005be:	test   %rax,%rax
  4005c1:	je     400b30 <main+0x640>
  4005c7:	incq   0x2a82(%rip)        # 403050 <meldra_heap_allocations>
  4005ce:	add    %r15,0x2a8b(%rip)        # 403060 <meldra_allocated_bytes>
  4005d5:	mov    0x2a8c(%rip),%rcx        # 403068 <meldra_bounds_checks>
  4005dc:	cmp    $0x4,%r13
  4005e0:	jae    4005e9 <main+0xf9>
  4005e2:	xor    %edx,%edx
  4005e4:	jmp    400953 <main+0x463>
  4005e9:	movabs $0x7ffffffffffffff0,%rsi
  4005f3:	movq   %rbx,%xmm2
  4005f8:	cmp    $0x10,%r13
  4005fc:	jae    400605 <main+0x115>
  4005fe:	xor    %edx,%edx
  400600:	jmp    40087b <main+0x38b>
  400605:	mov    %r13,%rdx
  400608:	and    %rsi,%rdx
  40060b:	movdqa %xmm2,0x30(%rsp)
  400611:	pshufd $0x44,%xmm2,%xmm0
  400616:	movdqa %xmm0,0x40(%rsp)
  40061c:	movdqa 0xceb(%rip),%xmm14        # 401310 <__dso_handle+0x8>
  400625:	movdqa 0xcf3(%rip),%xmm2        # 401320 <__dso_handle+0x18>
  40062d:	movdqa 0xcfb(%rip),%xmm3        # 401330 <__dso_handle+0x28>
  400635:	movdqa 0xd02(%rip),%xmm12        # 401340 <__dso_handle+0x38>
  40063e:	movdqa 0xd0a(%rip),%xmm6        # 401350 <__dso_handle+0x48>
  400646:	movdqa 0xd12(%rip),%xmm1        # 401360 <__dso_handle+0x58>
  40064e:	movdqa 0xd19(%rip),%xmm11        # 401370 <__dso_handle+0x68>
  400657:	movdqa 0xd20(%rip),%xmm9        # 401380 <__dso_handle+0x78>
  400660:	xor    %edi,%edi
  400662:	data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  400670:	movdqa %xmm3,0x10(%rsp)
  400676:	movdqa %xmm2,0x20(%rsp)
  40067c:	movdqa %xmm9,%xmm8
  400681:	psrlq  $0x3,%xmm8
  400687:	movdqa %xmm11,%xmm7
  40068c:	psrlq  $0x3,%xmm7
  400691:	movdqa %xmm1,%xmm10
  400696:	psrlq  $0x3,%xmm10
  40069c:	movdqa %xmm6,%xmm0
  4006a0:	psrlq  $0x3,%xmm0
  4006a5:	movdqa %xmm12,%xmm5
  4006aa:	psrlq  $0x3,%xmm5
  4006af:	movdqa 0x10(%rsp),%xmm15
  4006b6:	psrlq  $0x3,%xmm15
  4006bc:	movdqa %xmm2,%xmm4
  4006c0:	psrlq  $0x3,%xmm4
  4006c5:	movdqa %xmm9,%xmm2
  4006ca:	psllq  $0x4,%xmm2
  4006cf:	movdqa %xmm9,%xmm13
  4006d4:	movdqa 0x40(%rsp),%xmm3
  4006da:	paddq  %xmm3,%xmm13
  4006df:	paddq  %xmm2,%xmm13
  4006e4:	movdqa %xmm11,%xmm2
  4006e9:	psllq  $0x4,%xmm2
  4006ee:	paddq  %xmm8,%xmm13
  4006f3:	movdqa %xmm11,%xmm8
  4006f8:	paddq  %xmm3,%xmm8
  4006fd:	paddq  %xmm2,%xmm8
  400702:	movdqa %xmm1,%xmm2
  400706:	psllq  $0x4,%xmm2
  40070b:	paddq  %xmm7,%xmm8
  400710:	movdqa %xmm1,%xmm7
  400714:	paddq  %xmm3,%xmm7
  400718:	paddq  %xmm2,%xmm7
  40071c:	movdqa %xmm6,%xmm2
  400720:	psllq  $0x4,%xmm2
  400725:	paddq  %xmm10,%xmm7
  40072a:	movdqa %xmm6,%xmm10
  40072f:	paddq  %xmm3,%xmm10
  400734:	paddq  %xmm2,%xmm10
  400739:	movdqa %xmm12,%xmm2
  40073e:	psllq  $0x4,%xmm2
  400743:	paddq  %xmm0,%xmm10
  400748:	movdqa %xmm12,%xmm0
  40074d:	paddq  %xmm3,%xmm0
  400751:	paddq  %xmm2,%xmm0
  400755:	movdqa 0x10(%rsp),%xmm2
  40075b:	psllq  $0x4,%xmm2
  400760:	paddq  %xmm5,%xmm0
  400764:	movdqa 0x10(%rsp),%xmm5
  40076a:	paddq  %xmm3,%xmm5
  40076e:	paddq  %xmm2,%xmm5
  400772:	movdqa 0x20(%rsp),%xmm2
  400778:	psllq  $0x4,%xmm2
  40077d:	paddq  %xmm15,%xmm5
  400782:	movdqa 0x20(%rsp),%xmm15
  400789:	paddq  %xmm3,%xmm15
  40078e:	paddq  %xmm2,%xmm15
  400793:	movdqa %xmm14,%xmm2
  400798:	psrlq  $0x3,%xmm2
  40079d:	paddq  %xmm4,%xmm15
  4007a2:	movdqa %xmm14,%xmm4
  4007a7:	psllq  $0x4,%xmm14
  4007ad:	paddq  %xmm4,%xmm14
  4007b2:	paddq  %xmm3,%xmm2
  4007b6:	paddq  %xmm14,%xmm2
  4007bb:	movdqa %xmm4,%xmm14
  4007c0:	movdqa 0xbc8(%rip),%xmm3        # 401390 <__dso_handle+0x88>
  4007c8:	pand   %xmm3,%xmm13
  4007cd:	pand   %xmm3,%xmm8
  4007d2:	packuswb %xmm8,%xmm13
  4007d7:	pand   %xmm3,%xmm7
  4007db:	pand   %xmm3,%xmm10
  4007e0:	packuswb %xmm10,%xmm7
  4007e5:	packuswb %xmm7,%xmm13
  4007ea:	pand   %xmm3,%xmm0
  4007ee:	pand   %xmm3,%xmm5
  4007f2:	packuswb %xmm5,%xmm0
  4007f6:	pand   %xmm3,%xmm15
  4007fb:	pand   %xmm3,%xmm2
  4007ff:	packuswb %xmm2,%xmm15
  400804:	movdqa 0x20(%rsp),%xmm2
  40080a:	packuswb %xmm15,%xmm0
  40080f:	movdqa 0x10(%rsp),%xmm3
  400815:	packuswb %xmm0,%xmm13
  40081a:	paddb  0xb7d(%rip),%xmm13        # 4013a0 <__dso_handle+0x98>
  400823:	movdqu %xmm13,(%rax,%rdi,1)
  400829:	add    $0x10,%rdi
  40082d:	movdqa 0xb7b(%rip),%xmm0        # 4013b0 <__dso_handle+0xa8>
  400835:	paddq  %xmm0,%xmm9
  40083a:	paddq  %xmm0,%xmm11
  40083f:	paddq  %xmm0,%xmm1
  400843:	paddq  %xmm0,%xmm6
  400847:	paddq  %xmm0,%xmm12
  40084c:	paddq  %xmm0,%xmm3
  400850:	paddq  %xmm0,%xmm2
  400854:	paddq  %xmm0,%xmm14
  400859:	cmp    %rdi,%rdx
  40085c:	jne    400670 <main+0x180>
  400862:	cmp    %rdx,%r13
  400865:	movdqa 0x30(%rsp),%xmm2
  40086b:	je     400988 <main+0x498>
  400871:	test   $0xc,%r13b
  400875:	je     400953 <main+0x463>
  40087b:	mov    %rdx,%rdi
  40087e:	add    $0xc,%rsi
  400882:	mov    %rsi,%rdx
  400885:	and    %r13,%rdx
  400888:	movq   %rdi,%xmm0
  40088d:	pshufd $0x44,%xmm0,%xmm0
  400892:	movdqa 0xad6(%rip),%xmm1        # 401370 <__dso_handle+0x68>
  40089a:	por    %xmm0,%xmm1
  40089e:	por    0xada(%rip),%xmm0        # 401380 <__dso_handle+0x78>
  4008a6:	pshufd $0x44,%xmm2,%xmm2
  4008ab:	movdqa 0xadd(%rip),%xmm3        # 401390 <__dso_handle+0x88>
  4008b3:	movdqa 0xb05(%rip),%xmm4        # 4013c0 <__dso_handle+0xb8>
  4008bb:	movdqa 0xb0d(%rip),%xmm5        # 4013d0 <__dso_handle+0xc8>
  4008c3:	data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  4008d0:	movdqa %xmm0,%xmm6
  4008d4:	psrlq  $0x3,%xmm6
  4008d9:	movdqa %xmm1,%xmm7
  4008dd:	psrlq  $0x3,%xmm7
  4008e2:	movdqa %xmm1,%xmm8
  4008e7:	psllq  $0x4,%xmm8
  4008ed:	paddq  %xmm1,%xmm8
  4008f2:	movdqa %xmm0,%xmm9
  4008f7:	psllq  $0x4,%xmm9
  4008fd:	movdqa %xmm0,%xmm10
  400902:	paddq  %xmm2,%xmm10
  400907:	paddq  %xmm9,%xmm10
  40090c:	paddq  %xmm6,%xmm10
  400911:	paddq  %xmm2,%xmm7
  400915:	paddq  %xmm8,%xmm7
  40091a:	pand   %xmm3,%xmm10
  40091f:	pand   %xmm3,%xmm7
  400923:	packuswb %xmm7,%xmm10
  400928:	packuswb %xmm10,%xmm10
  40092d:	packuswb %xmm10,%xmm10
  400932:	paddb  %xmm4,%xmm10
  400937:	movd   %xmm10,(%rax,%rdi,1)
  40093d:	add    $0x4,%rdi
  400941:	paddq  %xmm5,%xmm0
  400945:	paddq  %xmm5,%xmm1
  400949:	cmp    %rdi,%rdx
  40094c:	jne    4008d0 <main+0x3e0>
  40094e:	cmp    %rdx,%r13
  400951:	je     400988 <main+0x498>
  400953:	mov    %edx,%edi
  400955:	shl    $0x4,%edi
  400958:	add    %edx,%edi
  40095a:	mov    %ebx,%esi
  40095c:	add    %dil,%sil
  40095f:	add    $0xac,%sil
  400963:	data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  400970:	mov    %edx,%edi
  400972:	shr    $0x3,%edi
  400975:	add    %sil,%dil
  400978:	mov    %dil,(%rax,%rdx,1)
  40097c:	inc    %rdx
  40097f:	add    $0x11,%sil
  400983:	cmp    %rdx,%r13
  400986:	jne    400970 <main+0x480>
  400988:	add    %r13,%rcx
  40098b:	mov    %rcx,0x26d6(%rip)        # 403068 <meldra_bounds_checks>
  400992:	mov    0x8(%rsp),%rdx
  400997:	sub    %r12,%r13
  40099a:	jb     400b1a <main+0x62a>
  4009a0:	cmp    %r13,%rdx
  4009a3:	ja     400b1a <main+0x62a>
  4009a9:	mov    %rdx,%rsi
  4009ac:	sub    %rbp,%rsi
  4009af:	jb     400b28 <main+0x638>
  4009b5:	add    %rax,%r12
  4009b8:	test   %rax,%rax
  4009bb:	cmove  %rax,%r12
  4009bf:	lea    (%r12,%rbp,1),%rcx
  4009c3:	test   %r12,%r12
  4009c6:	cmove  %r12,%rcx
  4009ca:	sub    %rdx,%rbp
  4009cd:	je     400a95 <main+0x5a5>
  4009d3:	mov    0x268e(%rip),%rdx        # 403068 <meldra_bounds_checks>
  4009da:	movabs $0x100000001b3,%rdi
  4009e4:	mov    %esi,%r8d
  4009e7:	and    $0x3,%r8d
  4009eb:	cmp    $0xfffffffffffffffc,%rbp
  4009ef:	jbe    4009f6 <main+0x506>
  4009f1:	xor    %r9d,%r9d
  4009f4:	jmp    400a5d <main+0x56d>
  4009f6:	mov    %rsi,%r10
  4009f9:	and    $0xfffffffffffffffc,%r10
  4009fd:	xor    %r9d,%r9d
  400a00:	movzbl (%rcx,%r9,1),%r11d
  400a05:	add    %r9,%r11
  400a08:	add    $0x1f,%r11
  400a0c:	xor    %rbx,%r11
  400a0f:	imul   %rdi,%r11
  400a13:	movzbl 0x1(%rcx,%r9,1),%ebx
  400a19:	add    %r9,%rbx
  400a1c:	add    $0x20,%rbx
  400a20:	xor    %r11,%rbx
  400a23:	imul   %rdi,%rbx
  400a27:	movzbl 0x2(%rcx,%r9,1),%r11d
  400a2d:	add    %r9,%r11
  400a30:	add    $0x21,%r11
  400a34:	xor    %rbx,%r11
  400a37:	imul   %rdi,%r11
  400a3b:	movzbl 0x3(%rcx,%r9,1),%ebx
  400a41:	add    %r9,%rbx
  400a44:	add    $0x22,%rbx
  400a48:	xor    %r11,%rbx
  400a4b:	imul   %rdi,%rbx
  400a4f:	add    $0x4,%r9
  400a53:	cmp    %r9,%r10
  400a56:	jne    400a00 <main+0x510>
  400a58:	test   %r8,%r8
  400a5b:	je     400a8b <main+0x59b>
  400a5d:	add    $0x1f,%r9
  400a61:	mov    %rbx,%r10
  400a64:	data16 data16 cs nopw 0x0(%rax,%rax,1)
  400a70:	movzbl -0x1f(%rcx,%r9,1),%ebx
  400a76:	add    %r9,%rbx
  400a79:	xor    %r10,%rbx
  400a7c:	imul   %rdi,%rbx
  400a80:	inc    %r9
  400a83:	mov    %rbx,%r10
  400a86:	dec    %r8
  400a89:	jne    400a70 <main+0x580>
  400a8b:	add    %rsi,%rdx
  400a8e:	mov    %rdx,0x25d3(%rip)        # 403068 <meldra_bounds_checks>
  400a95:	test   %rax,%rax
  400a98:	je     400aa9 <main+0x5b9>
  400a9a:	mov    %rax,%rdi
  400a9d:	call   400310 <free@plt>
  400aa2:	incq   0x25af(%rip)        # 403058 <meldra_heap_frees>
  400aa9:	mov    0x2590(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400ab0:	mov    0x2599(%rip),%rdx        # 403050 <meldra_heap_allocations>
  400ab7:	xor    %r14d,%r14d
  400aba:	mov    $0x4013fe,%esi
  400abf:	xor    %eax,%eax
  400ac1:	call   400350 <fprintf@plt>
  400ac6:	mov    0x2573(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400acd:	mov    0x2584(%rip),%rdx        # 403058 <meldra_heap_frees>
  400ad4:	mov    0x2585(%rip),%rcx        # 403060 <meldra_allocated_bytes>
  400adb:	mov    0x2586(%rip),%r9        # 403068 <meldra_bounds_checks>
  400ae2:	mov    $0x401416,%esi
  400ae7:	xor    %r8d,%r8d
  400aea:	xor    %eax,%eax
  400aec:	call   400350 <fprintf@plt>
  400af1:	mov    $0x40148f,%edi
  400af6:	mov    %rbx,%rsi
  400af9:	xor    %eax,%eax
  400afb:	call   400330 <printf@plt>
  400b00:	mov    %r14d,%eax
  400b03:	add    $0x58,%rsp
  400b07:	pop    %rbx
  400b08:	pop    %r12
  400b0a:	pop    %r13
  400b0c:	pop    %r14
  400b0e:	pop    %r15
  400b10:	pop    %rbp
  400b11:	ret
  400b12:	mov    %r15,%rdi
  400b15:	call   400380 <meldra_panic_bytes_allocation_overflow>
  400b1a:	mov    %r12,%rdi
  400b1d:	mov    %rdx,%rsi
  400b20:	mov    %r15,%rdx
  400b23:	call   4003d0 <meldra_panic_bytes_slice>
  400b28:	mov    %rbp,%rdi
  400b2b:	call   4003d0 <meldra_panic_bytes_slice>
  400b30:	call   4003a0 <meldra_panic_alloc>

Disassembly of section .fini:

0000000000400b38 <_fini>:
  400b38:	endbr64
  400b3c:	sub    $0x8,%rsp
  400b40:	add    $0x8,%rsp
  400b44:	ret
