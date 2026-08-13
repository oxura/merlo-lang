
benchmarks/meldra_bytes_call_boundary/abi/c/program:     file format elf64-x86-64


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

0000000000400320 <printf@plt>:
  400320:	jmp    *0x2ce2(%rip)        # 403008 <printf@GLIBC_2.2.5>
  400326:	push   $0x1
  40032b:	jmp    400300 <_init+0x20>

0000000000400330 <strtoull@plt>:
  400330:	jmp    *0x2cda(%rip)        # 403010 <strtoull@GLIBC_2.2.5>
  400336:	push   $0x2
  40033b:	jmp    400300 <_init+0x20>

0000000000400340 <fprintf@plt>:
  400340:	jmp    *0x2cd2(%rip)        # 403018 <fprintf@GLIBC_2.2.5>
  400346:	push   $0x3
  40034b:	jmp    400300 <_init+0x20>

0000000000400350 <malloc@plt>:
  400350:	jmp    *0x2cca(%rip)        # 403020 <malloc@GLIBC_2.2.5>
  400356:	push   $0x4
  40035b:	jmp    400300 <_init+0x20>

Disassembly of section .text:

0000000000400360 <_start>:
  400360:	endbr64
  400364:	xor    %ebp,%ebp
  400366:	mov    %rdx,%r9
  400369:	pop    %rsi
  40036a:	mov    %rsp,%rdx
  40036d:	and    $0xfffffffffffffff0,%rsp
  400371:	push   %rax
  400372:	push   %rsp
  400373:	xor    %r8d,%r8d
  400376:	xor    %ecx,%ecx
  400378:	mov    $0x400450,%rdi
  40037f:	call   *0x2c53(%rip)        # 402fd8 <__libc_start_main@GLIBC_2.34>
  400385:	hlt
  400386:	cs nopw 0x0(%rax,%rax,1)

0000000000400390 <_dl_relocate_static_pie>:
  400390:	endbr64
  400394:	ret
  400395:	cs nopw 0x0(%rax,%rax,1)
  40039f:	nop

00000000004003a0 <deregister_tm_clones>:
  4003a0:	mov    $0x403030,%eax
  4003a5:	cmp    $0x403030,%rax
  4003ab:	je     4003c0 <deregister_tm_clones+0x20>
  4003ad:	mov    $0x0,%eax
  4003b2:	test   %rax,%rax
  4003b5:	je     4003c0 <deregister_tm_clones+0x20>
  4003b7:	mov    $0x403030,%edi
  4003bc:	jmp    *%rax
  4003be:	xchg   %ax,%ax
  4003c0:	ret
  4003c1:	nopl   0x0(%rax)
  4003c5:	data16 cs nopw 0x0(%rax,%rax,1)

00000000004003d0 <register_tm_clones>:
  4003d0:	mov    $0x403030,%esi
  4003d5:	sub    $0x403030,%rsi
  4003dc:	mov    %rsi,%rax
  4003df:	shr    $0x3f,%rsi
  4003e3:	sar    $0x3,%rax
  4003e7:	add    %rax,%rsi
  4003ea:	sar    $1,%rsi
  4003ed:	je     400400 <register_tm_clones+0x30>
  4003ef:	mov    $0x0,%eax
  4003f4:	test   %rax,%rax
  4003f7:	je     400400 <register_tm_clones+0x30>
  4003f9:	mov    $0x403030,%edi
  4003fe:	jmp    *%rax
  400400:	ret
  400401:	nopl   0x0(%rax)
  400405:	data16 cs nopw 0x0(%rax,%rax,1)

0000000000400410 <__do_global_dtors_aux>:
  400410:	endbr64
  400414:	cmpb   $0x0,0x2c2d(%rip)        # 403048 <completed.0>
  40041b:	jne    400430 <__do_global_dtors_aux+0x20>
  40041d:	push   %rbp
  40041e:	mov    %rsp,%rbp
  400421:	call   4003a0 <deregister_tm_clones>
  400426:	movb   $0x1,0x2c1b(%rip)        # 403048 <completed.0>
  40042d:	pop    %rbp
  40042e:	ret
  40042f:	nop
  400430:	ret
  400431:	nopl   0x0(%rax)
  400435:	data16 cs nopw 0x0(%rax,%rax,1)

0000000000400440 <frame_dummy>:
  400440:	endbr64
  400444:	jmp    4003d0 <register_tm_clones>
  400446:	cs nopw 0x0(%rax,%rax,1)

0000000000400450 <main>:
  400450:	mov    $0x2,%eax
  400455:	cmp    $0x6,%edi
  400458:	jne    400554 <main+0x104>
  40045e:	push   %rbp
  40045f:	push   %r15
  400461:	push   %r14
  400463:	push   %r13
  400465:	push   %r12
  400467:	push   %rbx
  400468:	sub    $0xf8,%rsp
  40046f:	mov    0x8(%rsi),%rdi
  400473:	mov    %rsi,%rbx
  400476:	xor    %esi,%esi
  400478:	mov    $0xa,%edx
  40047d:	call   400330 <strtoull@plt>
  400482:	mov    %rax,%r14
  400485:	mov    0x10(%rbx),%rdi
  400489:	xor    %esi,%esi
  40048b:	mov    $0xa,%edx
  400490:	call   400330 <strtoull@plt>
  400495:	mov    %rax,%r15
  400498:	mov    0x18(%rbx),%rdi
  40049c:	xor    %esi,%esi
  40049e:	mov    $0xa,%edx
  4004a3:	call   400330 <strtoull@plt>
  4004a8:	mov    %rax,0x50(%rsp)
  4004ad:	mov    0x20(%rbx),%rdi
  4004b1:	xor    %esi,%esi
  4004b3:	mov    $0xa,%edx
  4004b8:	call   400330 <strtoull@plt>
  4004bd:	mov    %rax,%r12
  4004c0:	mov    0x28(%rbx),%rdi
  4004c4:	xor    %esi,%esi
  4004c6:	mov    $0xa,%edx
  4004cb:	call   400330 <strtoull@plt>
  4004d0:	mov    %rax,%rcx
  4004d3:	mov    $0x3,%eax
  4004d8:	mov    %r14,%rbx
  4004db:	mov    %rcx,0x58(%rsp)
  4004e0:	sub    %rcx,%rbx
  4004e3:	jb     400543 <main+0xf3>
  4004e5:	cmp    %rbx,%r12
  4004e8:	ja     400543 <main+0xf3>
  4004ea:	test   %r14,%r14
  4004ed:	je     4004fc <main+0xac>
  4004ef:	mov    %r14,%rdi
  4004f2:	call   400350 <malloc@plt>
  4004f7:	mov    %rax,%rbp
  4004fa:	jmp    4004fe <main+0xae>
  4004fc:	xor    %ebp,%ebp
  4004fe:	mov    %rbp,0x90(%rsp)
  400506:	mov    %r14,0x98(%rsp)
  40050e:	mov    %r14,0xa0(%rsp)
  400516:	movb   $0x1,0xa8(%rsp)
  40051e:	movl   $0x0,0xa9(%rsp)
  400529:	movl   $0x0,0xac(%rsp)
  400534:	test   %r14,%r14
  400537:	je     400555 <main+0x105>
  400539:	mov    $0x4,%eax
  40053e:	test   %rbp,%rbp
  400541:	jne    400555 <main+0x105>
  400543:	add    $0xf8,%rsp
  40054a:	pop    %rbx
  40054b:	pop    %r12
  40054d:	pop    %r13
  40054f:	pop    %r14
  400551:	pop    %r15
  400553:	pop    %rbp
  400554:	ret
  400555:	test   %r14,%r14
  400558:	je     4009d6 <main+0x586>
  40055e:	cmp    $0x8,%r14
  400562:	jae    40056b <main+0x11b>
  400564:	xor    %eax,%eax
  400566:	jmp    4009ad <main+0x55d>
  40056b:	movq   %r15,%xmm0
  400570:	cmp    $0x10,%r14
  400574:	jae    4006da <main+0x28a>
  40057a:	xor    %eax,%eax
  40057c:	mov    %rax,%rcx
  40057f:	mov    %r14,%rax
  400582:	and    $0xfffffffffffffff8,%rax
  400586:	pshufd $0x44,%xmm0,%xmm0
  40058b:	movq   %rcx,%xmm1
  400590:	pshufd $0x44,%xmm1,%xmm1
  400595:	movdqa 0xd43(%rip),%xmm2        # 4012e0 <__dso_handle+0x48>
  40059d:	por    %xmm1,%xmm2
  4005a1:	movdqa 0xd47(%rip),%xmm3        # 4012f0 <__dso_handle+0x58>
  4005a9:	por    %xmm1,%xmm3
  4005ad:	movdqa 0xd4b(%rip),%xmm4        # 401300 <__dso_handle+0x68>
  4005b5:	por    %xmm1,%xmm4
  4005b9:	por    0xd4f(%rip),%xmm1        # 401310 <__dso_handle+0x78>
  4005c1:	movdqa 0xd57(%rip),%xmm5        # 401320 <__dso_handle+0x88>
  4005c9:	movdqa 0xd6f(%rip),%xmm6        # 401340 <__dso_handle+0xa8>
  4005d1:	data16 data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  4005e0:	movdqa %xmm2,%xmm7
  4005e4:	psllq  $0x4,%xmm7
  4005e9:	paddq  %xmm2,%xmm7
  4005ed:	movdqa %xmm3,%xmm8
  4005f2:	psllq  $0x4,%xmm8
  4005f8:	movdqa %xmm4,%xmm9
  4005fd:	psllq  $0x4,%xmm9
  400603:	movdqa %xmm1,%xmm10
  400608:	psllq  $0x4,%xmm10
  40060e:	movdqa %xmm1,%xmm11
  400613:	paddq  %xmm0,%xmm11
  400618:	paddq  %xmm10,%xmm11
  40061d:	movdqa %xmm4,%xmm10
  400622:	paddq  %xmm0,%xmm10
  400627:	paddq  %xmm9,%xmm10
  40062c:	movdqa %xmm3,%xmm9
  400631:	paddq  %xmm0,%xmm9
  400636:	paddq  %xmm8,%xmm9
  40063b:	movdqa %xmm1,%xmm8
  400640:	psrlq  $0x3,%xmm8
  400646:	paddq  %xmm11,%xmm8
  40064b:	movdqa %xmm4,%xmm11
  400650:	psrlq  $0x3,%xmm11
  400656:	paddq  %xmm10,%xmm11
  40065b:	movdqa %xmm3,%xmm10
  400660:	psrlq  $0x3,%xmm10
  400666:	paddq  %xmm9,%xmm10
  40066b:	movdqa %xmm2,%xmm9
  400670:	psrlq  $0x3,%xmm9
  400676:	paddq  %xmm0,%xmm9
  40067b:	paddq  %xmm7,%xmm9
  400680:	pand   %xmm5,%xmm8
  400685:	pand   %xmm5,%xmm11
  40068a:	packuswb %xmm11,%xmm8
  40068f:	pand   %xmm5,%xmm10
  400694:	pand   %xmm5,%xmm9
  400699:	packuswb %xmm9,%xmm10
  40069e:	packuswb %xmm10,%xmm8
  4006a3:	packuswb %xmm8,%xmm8
  4006a8:	movq   %xmm8,0x0(%rbp,%rcx,1)
  4006af:	add    $0x8,%rcx
  4006b3:	paddq  %xmm6,%xmm1
  4006b7:	paddq  %xmm6,%xmm4
  4006bb:	paddq  %xmm6,%xmm3
  4006bf:	paddq  %xmm6,%xmm2
  4006c3:	cmp    %rcx,%rax
  4006c6:	jne    4005e0 <main+0x190>
  4006cc:	cmp    %rax,%r14
  4006cf:	jne    4009ad <main+0x55d>
  4006d5:	jmp    4009d6 <main+0x586>
  4006da:	mov    %r14,%rax
  4006dd:	and    $0xfffffffffffffff0,%rax
  4006e1:	movdqa %xmm0,0xb0(%rsp)
  4006ea:	pshufd $0x44,%xmm0,%xmm0
  4006ef:	movdqa %xmm0,0xc0(%rsp)
  4006f8:	movdqa 0xba0(%rip),%xmm1        # 4012a0 <__dso_handle+0x8>
  400700:	movdqa 0xba8(%rip),%xmm0        # 4012b0 <__dso_handle+0x18>
  400708:	movdqa 0xbb0(%rip),%xmm3        # 4012c0 <__dso_handle+0x28>
  400710:	movdqa 0xbb8(%rip),%xmm4        # 4012d0 <__dso_handle+0x38>
  400718:	movdqa 0xbc0(%rip),%xmm5        # 4012e0 <__dso_handle+0x48>
  400720:	movdqa 0xbc7(%rip),%xmm10        # 4012f0 <__dso_handle+0x58>
  400729:	movdqa 0xbcf(%rip),%xmm6        # 401300 <__dso_handle+0x68>
  400731:	movdqa 0xbd7(%rip),%xmm2        # 401310 <__dso_handle+0x78>
  400739:	xor    %ecx,%ecx
  40073b:	movdqa 0xc0(%rsp),%xmm7
  400744:	data16 data16 cs nopw 0x0(%rax,%rax,1)
  400750:	movdqa %xmm6,0x60(%rsp)
  400756:	movdqa %xmm5,0x70(%rsp)
  40075c:	movdqa %xmm4,0x80(%rsp)
  400765:	movdqa %xmm3,0x40(%rsp)
  40076b:	movdqa %xmm0,0x20(%rsp)
  400771:	movdqa %xmm1,0x30(%rsp)
  400777:	movdqa 0x30(%rsp),%xmm12
  40077e:	psllq  $0x4,%xmm12
  400784:	paddq  0x30(%rsp),%xmm12
  40078b:	movdqa 0x20(%rsp),%xmm13
  400792:	psllq  $0x4,%xmm13
  400798:	movdqa 0x40(%rsp),%xmm3
  40079e:	psllq  $0x4,%xmm3
  4007a3:	movdqa 0x80(%rsp),%xmm4
  4007ac:	psllq  $0x4,%xmm4
  4007b1:	movdqa 0x70(%rsp),%xmm6
  4007b7:	psllq  $0x4,%xmm6
  4007bc:	movdqa %xmm10,%xmm8
  4007c1:	psllq  $0x4,%xmm8
  4007c7:	movdqa 0x60(%rsp),%xmm14
  4007ce:	psllq  $0x4,%xmm14
  4007d4:	movdqa %xmm2,%xmm15
  4007d9:	psllq  $0x4,%xmm15
  4007df:	movdqa %xmm2,%xmm9
  4007e4:	paddq  %xmm7,%xmm9
  4007e9:	paddq  %xmm15,%xmm9
  4007ee:	movdqa 0x60(%rsp),%xmm15
  4007f5:	paddq  %xmm7,%xmm15
  4007fa:	paddq  %xmm14,%xmm15
  4007ff:	movdqa %xmm10,%xmm14
  400804:	paddq  %xmm7,%xmm14
  400809:	paddq  %xmm8,%xmm14
  40080e:	movdqa 0x70(%rsp),%xmm1
  400814:	paddq  %xmm7,%xmm1
  400818:	paddq  %xmm6,%xmm1
  40081c:	movdqa 0x80(%rsp),%xmm5
  400825:	paddq  %xmm7,%xmm5
  400829:	paddq  %xmm4,%xmm5
  40082d:	movdqa 0x40(%rsp),%xmm11
  400834:	paddq  %xmm7,%xmm11
  400839:	paddq  %xmm3,%xmm11
  40083e:	movdqa 0x20(%rsp),%xmm0
  400844:	paddq  %xmm7,%xmm0
  400848:	paddq  %xmm13,%xmm0
  40084d:	movdqa %xmm2,%xmm13
  400852:	psrlq  $0x3,%xmm13
  400858:	paddq  %xmm9,%xmm13
  40085d:	movdqa 0x60(%rsp),%xmm8
  400864:	psrlq  $0x3,%xmm8
  40086a:	paddq  %xmm15,%xmm8
  40086f:	movdqa %xmm10,%xmm15
  400874:	psrlq  $0x3,%xmm15
  40087a:	paddq  %xmm14,%xmm15
  40087f:	movdqa 0x70(%rsp),%xmm6
  400885:	psrlq  $0x3,%xmm6
  40088a:	paddq  %xmm1,%xmm6
  40088e:	movdqa 0x80(%rsp),%xmm14
  400898:	psrlq  $0x3,%xmm14
  40089e:	paddq  %xmm5,%xmm14
  4008a3:	movdqa 0x40(%rsp),%xmm4
  4008a9:	psrlq  $0x3,%xmm4
  4008ae:	paddq  %xmm11,%xmm4
  4008b3:	movdqa 0x20(%rsp),%xmm3
  4008b9:	psrlq  $0x3,%xmm3
  4008be:	paddq  %xmm0,%xmm3
  4008c2:	movdqa 0x30(%rsp),%xmm0
  4008c8:	psrlq  $0x3,%xmm0
  4008cd:	paddq  %xmm7,%xmm0
  4008d1:	paddq  %xmm12,%xmm0
  4008d6:	movdqa 0x30(%rsp),%xmm1
  4008dc:	movdqa 0xa3b(%rip),%xmm9        # 401320 <__dso_handle+0x88>
  4008e5:	pand   %xmm9,%xmm13
  4008ea:	pand   %xmm9,%xmm8
  4008ef:	packuswb %xmm8,%xmm13
  4008f4:	pand   %xmm9,%xmm15
  4008f9:	pand   %xmm9,%xmm6
  4008fe:	packuswb %xmm6,%xmm15
  400903:	movdqa 0x70(%rsp),%xmm5
  400909:	packuswb %xmm15,%xmm13
  40090e:	pand   %xmm9,%xmm14
  400913:	pand   %xmm9,%xmm4
  400918:	packuswb %xmm4,%xmm14
  40091d:	movdqa 0x80(%rsp),%xmm4
  400926:	pand   %xmm9,%xmm3
  40092b:	pand   %xmm9,%xmm0
  400930:	packuswb %xmm0,%xmm3
  400934:	packuswb %xmm3,%xmm14
  400939:	movdqa 0x40(%rsp),%xmm3
  40093f:	packuswb %xmm14,%xmm13
  400944:	movdqa 0x60(%rsp),%xmm6
  40094a:	movdqu %xmm13,0x0(%rbp,%rcx,1)
  400951:	movdqa 0x20(%rsp),%xmm0
  400957:	add    $0x10,%rcx
  40095b:	movdqa 0x9cc(%rip),%xmm8        # 401330 <__dso_handle+0x98>
  400964:	paddq  %xmm8,%xmm2
  400969:	paddq  %xmm8,%xmm6
  40096e:	paddq  %xmm8,%xmm10
  400973:	paddq  %xmm8,%xmm5
  400978:	paddq  %xmm8,%xmm4
  40097d:	paddq  %xmm8,%xmm3
  400982:	paddq  %xmm8,%xmm0
  400987:	paddq  %xmm8,%xmm1
  40098c:	cmp    %rcx,%rax
  40098f:	jne    400750 <main+0x300>
  400995:	cmp    %rax,%r14
  400998:	movdqa 0xb0(%rsp),%xmm0
  4009a1:	je     4009d6 <main+0x586>
  4009a3:	test   $0x8,%r14b
  4009a7:	jne    40057c <main+0x12c>
  4009ad:	mov    %eax,%edx
  4009af:	shl    $0x4,%edx
  4009b2:	add    %eax,%edx
  4009b4:	mov    %r15d,%ecx
  4009b7:	add    %dl,%cl
  4009b9:	nopl   0x0(%rax)
  4009c0:	mov    %eax,%edx
  4009c2:	shr    $0x3,%edx
  4009c5:	add    %cl,%dl
  4009c7:	mov    %dl,0x0(%rbp,%rax,1)
  4009cb:	inc    %rax
  4009ce:	add    $0x11,%cl
  4009d1:	cmp    %rax,%r14
  4009d4:	jne    4009c0 <main+0x570>
  4009d6:	mov    %r12,0x20(%rsp)
  4009db:	mov    %r14,0x40(%rsp)
  4009e0:	mov    %r15,0x30(%rsp)
  4009e5:	mov    %r15,%r14
  4009e8:	cmpq   $0x0,0x50(%rsp)
  4009ee:	jne    400a7d <main+0x62d>
  4009f4:	movups 0x90(%rsp),%xmm0
  4009fc:	movups 0xa0(%rsp),%xmm1
  400a04:	movups %xmm1,0x10(%rsp)
  400a09:	movups %xmm0,(%rsp)
  400a0d:	lea    0xd8(%rsp),%rdi
  400a15:	mov    0x30(%rsp),%rsi
  400a1a:	call   400bb0 <transform>
  400a1f:	mov    0xd8(%rsp),%r15
  400a27:	mov    0x20(%rsp),%rdi
  400a2c:	add    %r15,%rdi
  400a2f:	mov    0x58(%rsp),%rsi
  400a34:	mov    %r14,%rdx
  400a37:	call   400af0 <scan>
  400a3c:	mov    %rax,%r14
  400a3f:	add    0xe0(%rsp),%r14
  400a47:	mov    %r15,%rdi
  400a4a:	call   400310 <free@plt>
  400a4f:	mov    $0x401360,%edi
  400a54:	mov    %r14,%rsi
  400a57:	xor    %eax,%eax
  400a59:	call   400320 <printf@plt>
  400a5e:	mov    0x25db(%rip),%rdi        # 403040 <stderr@GLIBC_2.2.5>
  400a65:	mov    $0x401365,%esi
  400a6a:	mov    0x40(%rsp),%rdx
  400a6f:	xor    %eax,%eax
  400a71:	call   400340 <fprintf@plt>
  400a76:	xor    %eax,%eax
  400a78:	jmp    400543 <main+0xf3>
  400a7d:	inc    %rbx
  400a80:	xor    %r12d,%r12d
  400a83:	mov    0x20(%rsp),%r15
  400a88:	mov    0x30(%rsp),%r14
  400a8d:	jmp    400ad3 <main+0x683>
  400a8f:	nop
  400a90:	mov    %r15,%rax
  400a93:	xor    %edx,%edx
  400a95:	div    %rbx
  400a98:	mov    %rdx,%r13
  400a9b:	mov    %rbp,%rdi
  400a9e:	add    %r13,%rdi
  400aa1:	mov    0x58(%rsp),%rsi
  400aa6:	mov    %r14,%rdx
  400aa9:	call   400af0 <scan>
  400aae:	mov    %rax,%r14
  400ab1:	add    0x0(%rbp,%r13,1),%al
  400ab6:	movzbl %al,%eax
  400ab9:	add    %r12d,%eax
  400abc:	mov    %al,0x0(%rbp,%r13,1)
  400ac1:	inc    %r12
  400ac4:	add    $0x61,%r15
  400ac8:	cmp    %r12,0x50(%rsp)
  400acd:	je     4009f4 <main+0x5a4>
  400ad3:	mov    %r15,%rax
  400ad6:	or     %rbx,%rax
  400ad9:	shr    $0x20,%rax
  400add:	jne    400a90 <main+0x640>
  400adf:	mov    %r15d,%eax
  400ae2:	xor    %edx,%edx
  400ae4:	div    %ebx
  400ae6:	mov    %edx,%r13d
  400ae9:	jmp    400a9b <main+0x64b>
  400aeb:	nopl   0x0(%rax,%rax,1)

0000000000400af0 <scan>:
  400af0:	mov    %rdx,%rax
  400af3:	test   %rsi,%rsi
  400af6:	je     400bab <scan+0xbb>
  400afc:	movabs $0x100000001b3,%rcx
  400b06:	mov    %esi,%edx
  400b08:	and    $0x3,%edx
  400b0b:	cmp    $0x4,%rsi
  400b0f:	jae    400b16 <scan+0x26>
  400b11:	xor    %r8d,%r8d
  400b14:	jmp    400b7c <scan+0x8c>
  400b16:	and    $0xfffffffffffffffc,%rsi
  400b1a:	xor    %r8d,%r8d
  400b1d:	nopl   (%rax)
  400b20:	movzbl (%rdi,%r8,1),%r9d
  400b25:	add    %r8,%r9
  400b28:	inc    %r9
  400b2b:	xor    %rax,%r9
  400b2e:	imul   %rcx,%r9
  400b32:	movzbl 0x1(%rdi,%r8,1),%eax
  400b38:	add    %r8,%rax
  400b3b:	add    $0x2,%rax
  400b3f:	xor    %r9,%rax
  400b42:	imul   %rcx,%rax
  400b46:	movzbl 0x2(%rdi,%r8,1),%r9d
  400b4c:	add    %r8,%r9
  400b4f:	add    $0x3,%r9
  400b53:	xor    %rax,%r9
  400b56:	imul   %rcx,%r9
  400b5a:	movzbl 0x3(%rdi,%r8,1),%eax
  400b60:	add    %r8,%rax
  400b63:	add    $0x4,%rax
  400b67:	add    $0x4,%r8
  400b6b:	xor    %r9,%rax
  400b6e:	imul   %rcx,%rax
  400b72:	cmp    %r8,%rsi
  400b75:	jne    400b20 <scan+0x30>
  400b77:	test   %rdx,%rdx
  400b7a:	je     400bab <scan+0xbb>
  400b7c:	inc    %r8
  400b7f:	mov    %rax,%rsi
  400b82:	data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)
  400b90:	movzbl -0x1(%rdi,%r8,1),%eax
  400b96:	add    %r8,%rax
  400b99:	xor    %rsi,%rax
  400b9c:	imul   %rcx,%rax
  400ba0:	inc    %r8
  400ba3:	mov    %rax,%rsi
  400ba6:	dec    %rdx
  400ba9:	jne    400b90 <scan+0xa0>
  400bab:	ret
  400bac:	nopl   0x0(%rax)

0000000000400bb0 <transform>:
  400bb0:	lea    0x8(%rsp),%rax
  400bb5:	mov    0x10(%rsp),%rcx
  400bba:	test   %rcx,%rcx
  400bbd:	je     400df0 <transform+0x240>
  400bc3:	mov    (%rax),%rdx
  400bc6:	cmp    $0x4,%rcx
  400bca:	jae    400bd4 <transform+0x24>
  400bcc:	xor    %r8d,%r8d
  400bcf:	jmp    400dca <transform+0x21a>
  400bd4:	movq   %rsi,%xmm0
  400bd9:	cmp    $0x10,%rcx
  400bdd:	jae    400be7 <transform+0x37>
  400bdf:	xor    %r8d,%r8d
  400be2:	jmp    400d43 <transform+0x193>
  400be7:	mov    %rcx,%r8
  400bea:	and    $0xfffffffffffffff0,%r8
  400bee:	pshufd $0x44,%xmm0,%xmm1
  400bf3:	movdqa 0x6a5(%rip),%xmm2        # 4012a0 <__dso_handle+0x8>
  400bfb:	movdqa 0x6ad(%rip),%xmm3        # 4012b0 <__dso_handle+0x18>
  400c03:	movdqa 0x6b5(%rip),%xmm4        # 4012c0 <__dso_handle+0x28>
  400c0b:	movdqa 0x6bd(%rip),%xmm5        # 4012d0 <__dso_handle+0x38>
  400c13:	movdqa 0x6c5(%rip),%xmm6        # 4012e0 <__dso_handle+0x48>
  400c1b:	movdqa 0x6cd(%rip),%xmm7        # 4012f0 <__dso_handle+0x58>
  400c23:	movdqa 0x6d4(%rip),%xmm8        # 401300 <__dso_handle+0x68>
  400c2c:	movdqa 0x6db(%rip),%xmm9        # 401310 <__dso_handle+0x78>
  400c35:	xor    %r9d,%r9d
  400c38:	movdqa 0x6df(%rip),%xmm10        # 401320 <__dso_handle+0x88>
  400c41:	movdqa 0x6e6(%rip),%xmm11        # 401330 <__dso_handle+0x98>
  400c4a:	nopw   0x0(%rax,%rax,1)
  400c50:	movdqa %xmm2,%xmm12
  400c55:	paddq  %xmm1,%xmm12
  400c5a:	movdqa %xmm6,%xmm14
  400c5f:	paddq  %xmm1,%xmm14
  400c64:	movdqa %xmm8,%xmm15
  400c69:	paddq  %xmm1,%xmm15
  400c6e:	movdqa %xmm9,%xmm13
  400c73:	paddq  %xmm1,%xmm13
  400c78:	pand   %xmm10,%xmm13
  400c7d:	pand   %xmm10,%xmm15
  400c82:	packuswb %xmm15,%xmm13
  400c87:	movdqa %xmm7,%xmm15
  400c8c:	paddq  %xmm1,%xmm15
  400c91:	pand   %xmm10,%xmm15
  400c96:	pand   %xmm10,%xmm14
  400c9b:	packuswb %xmm14,%xmm15
  400ca0:	movdqa %xmm4,%xmm14
  400ca5:	paddq  %xmm1,%xmm14
  400caa:	packuswb %xmm15,%xmm13
  400caf:	movdqa %xmm5,%xmm15
  400cb4:	paddq  %xmm1,%xmm15
  400cb9:	pand   %xmm10,%xmm15
  400cbe:	pand   %xmm10,%xmm14
  400cc3:	packuswb %xmm14,%xmm15
  400cc8:	movdqa %xmm3,%xmm14
  400ccd:	paddq  %xmm1,%xmm14
  400cd2:	pand   %xmm10,%xmm14
  400cd7:	pand   %xmm10,%xmm12
  400cdc:	packuswb %xmm12,%xmm14
  400ce1:	packuswb %xmm14,%xmm15
  400ce6:	packuswb %xmm15,%xmm13
  400ceb:	movdqu (%rdx,%r9,1),%xmm12
  400cf1:	pxor   %xmm12,%xmm13
  400cf6:	movdqu %xmm13,(%rdx,%r9,1)
  400cfc:	add    $0x10,%r9
  400d00:	paddq  %xmm11,%xmm9
  400d05:	paddq  %xmm11,%xmm8
  400d0a:	paddq  %xmm11,%xmm7
  400d0f:	paddq  %xmm11,%xmm6
  400d14:	paddq  %xmm11,%xmm5
  400d19:	paddq  %xmm11,%xmm4
  400d1e:	paddq  %xmm11,%xmm3
  400d23:	paddq  %xmm11,%xmm2
  400d28:	cmp    %r9,%r8
  400d2b:	jne    400c50 <transform+0xa0>
  400d31:	cmp    %r8,%rcx
  400d34:	je     400df0 <transform+0x240>
  400d3a:	test   $0xc,%cl
  400d3d:	je     400dca <transform+0x21a>
  400d43:	mov    %r8,%r9
  400d46:	mov    %rcx,%r8
  400d49:	and    $0xfffffffffffffffc,%r8
  400d4d:	pshufd $0x44,%xmm0,%xmm0
  400d52:	movq   %r9,%xmm1
  400d57:	pshufd $0x44,%xmm1,%xmm1
  400d5c:	movdqa 0x59c(%rip),%xmm2        # 401300 <__dso_handle+0x68>
  400d64:	por    %xmm1,%xmm2
  400d68:	por    0x5a0(%rip),%xmm1        # 401310 <__dso_handle+0x78>
  400d70:	movdqa 0x5a8(%rip),%xmm3        # 401320 <__dso_handle+0x88>
  400d78:	movdqa 0x5d0(%rip),%xmm4        # 401350 <__dso_handle+0xb8>
  400d80:	movd   (%rdx,%r9,1),%xmm5
  400d86:	movdqa %xmm2,%xmm6
  400d8a:	paddq  %xmm0,%xmm6
  400d8e:	movdqa %xmm1,%xmm7
  400d92:	paddq  %xmm0,%xmm7
  400d96:	pand   %xmm3,%xmm7
  400d9a:	pand   %xmm3,%xmm6
  400d9e:	packuswb %xmm6,%xmm7
  400da2:	packuswb %xmm7,%xmm7
  400da6:	packuswb %xmm7,%xmm7
  400daa:	pxor   %xmm5,%xmm7
  400dae:	movd   %xmm7,(%rdx,%r9,1)
  400db4:	add    $0x4,%r9
  400db8:	paddq  %xmm4,%xmm1
  400dbc:	paddq  %xmm4,%xmm2
  400dc0:	cmp    %r9,%r8
  400dc3:	jne    400d80 <transform+0x1d0>
  400dc5:	cmp    %r8,%rcx
  400dc8:	je     400df0 <transform+0x240>
  400dca:	sub    %r8,%rcx
  400dcd:	add    %r8,%rsi
  400dd0:	add    %r8,%rdx
  400dd3:	xor    %r8d,%r8d
  400dd6:	cs nopw 0x0(%rax,%rax,1)
  400de0:	lea    (%rsi,%r8,1),%r9d
  400de4:	xor    %r9b,(%rdx,%r8,1)
  400de8:	inc    %r8
  400deb:	cmp    %r8,%rcx
  400dee:	jne    400de0 <transform+0x230>
  400df0:	movups (%rax),%xmm0
  400df3:	movups 0x10(%rax),%xmm1
  400df7:	movups %xmm1,0x10(%rdi)
  400dfb:	movups %xmm0,(%rdi)
  400dfe:	ret

Disassembly of section .fini:

0000000000400e00 <_fini>:
  400e00:	endbr64
  400e04:	sub    $0x8,%rsp
  400e08:	add    $0x8,%rsp
  400e0c:	ret
