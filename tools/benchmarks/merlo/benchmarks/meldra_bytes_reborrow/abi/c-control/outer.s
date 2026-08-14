
tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow/abi/c-control/program:     file format elf64-x86-64


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

0000000000400340 <malloc@plt>:
  400340:	jmp    *0x2cd2(%rip)        # 403018 <malloc@GLIBC_2.2.5>
  400346:	push   $0x3
  40034b:	jmp    400300 <_init+0x20>

Disassembly of section .text:

0000000000400350 <_start>:
  400350:	endbr64
  400354:	xor    %ebp,%ebp
  400356:	mov    %rdx,%r9
  400359:	pop    %rsi
  40035a:	mov    %rsp,%rdx
  40035d:	and    $0xfffffffffffffff0,%rsp
  400361:	push   %rax
  400362:	push   %rsp
  400363:	xor    %r8d,%r8d
  400366:	xor    %ecx,%ecx
  400368:	mov    $0x400440,%rdi
  40036f:	call   *0x2c63(%rip)        # 402fd8 <__libc_start_main@GLIBC_2.34>
  400375:	hlt
  400376:	cs nopw 0x0(%rax,%rax,1)

0000000000400380 <_dl_relocate_static_pie>:
  400380:	endbr64
  400384:	ret
  400385:	cs nopw 0x0(%rax,%rax,1)
  40038f:	nop

0000000000400390 <deregister_tm_clones>:
  400390:	mov    $0x403028,%eax
  400395:	cmp    $0x403028,%rax
  40039b:	je     4003b0 <deregister_tm_clones+0x20>
  40039d:	mov    $0x0,%eax
  4003a2:	test   %rax,%rax
  4003a5:	je     4003b0 <deregister_tm_clones+0x20>
  4003a7:	mov    $0x403028,%edi
  4003ac:	jmp    *%rax
  4003ae:	xchg   %ax,%ax
  4003b0:	ret
  4003b1:	nopl   0x0(%rax)
  4003b5:	data16 cs nopw 0x0(%rax,%rax,1)

00000000004003c0 <register_tm_clones>:
  4003c0:	mov    $0x403028,%esi
  4003c5:	sub    $0x403028,%rsi
  4003cc:	mov    %rsi,%rax
  4003cf:	shr    $0x3f,%rsi
  4003d3:	sar    $0x3,%rax
  4003d7:	add    %rax,%rsi
  4003da:	sar    $1,%rsi
  4003dd:	je     4003f0 <register_tm_clones+0x30>
  4003df:	mov    $0x0,%eax
  4003e4:	test   %rax,%rax
  4003e7:	je     4003f0 <register_tm_clones+0x30>
  4003e9:	mov    $0x403028,%edi
  4003ee:	jmp    *%rax
  4003f0:	ret
  4003f1:	nopl   0x0(%rax)
  4003f5:	data16 cs nopw 0x0(%rax,%rax,1)

0000000000400400 <__do_global_dtors_aux>:
  400400:	endbr64
  400404:	cmpb   $0x0,0x2c19(%rip)        # 403024 <completed.0>
  40040b:	jne    400420 <__do_global_dtors_aux+0x20>
  40040d:	push   %rbp
  40040e:	mov    %rsp,%rbp
  400411:	call   400390 <deregister_tm_clones>
  400416:	movb   $0x1,0x2c07(%rip)        # 403024 <completed.0>
  40041d:	pop    %rbp
  40041e:	ret
  40041f:	nop
  400420:	ret
  400421:	nopl   0x0(%rax)
  400425:	data16 cs nopw 0x0(%rax,%rax,1)

0000000000400430 <frame_dummy>:
  400430:	endbr64
  400434:	jmp    4003c0 <register_tm_clones>
  400436:	cs nopw 0x0(%rax,%rax,1)

0000000000400440 <main>:
  400440:	push   %r15
  400442:	push   %r14
  400444:	push   %rbx
  400445:	mov    $0x2,%ebx
  40044a:	cmp    $0x2,%edi
  40044d:	jne    400576 <main+0x136>
  400453:	mov    0x8(%rsi),%rdi
  400457:	xor    %ebx,%ebx
  400459:	xor    %esi,%esi
  40045b:	mov    $0xa,%edx
  400460:	call   400330 <strtoull@plt>
  400465:	mov    %rax,%r14
  400468:	mov    $0x0,%r15d
  40046e:	test   %rax,%rax
  400471:	je     400551 <main+0x111>
  400477:	mov    %r14,%rdi
  40047a:	call   400340 <malloc@plt>
  40047f:	test   %rax,%rax
  400482:	je     400494 <main+0x54>
  400484:	mov    %rax,%r15
  400487:	cmp    $0x4,%r14
  40048b:	jae    40049e <main+0x5e>
  40048d:	xor    %eax,%eax
  40048f:	jmp    400545 <main+0x105>
  400494:	mov    $0x3,%ebx
  400499:	jmp    400576 <main+0x136>
  40049e:	cmp    $0x20,%r14
  4004a2:	jae    4004a8 <main+0x68>
  4004a4:	xor    %eax,%eax
  4004a6:	jmp    4004fd <main+0xbd>
  4004a8:	mov    %r14,%rax
  4004ab:	and    $0xffffffffffffffe0,%rax
  4004af:	movdqa 0xd79(%rip),%xmm0        # 401230 <__dso_handle+0x8>
  4004b7:	xor    %ecx,%ecx
  4004b9:	movdqa 0xd7f(%rip),%xmm1        # 401240 <__dso_handle+0x18>
  4004c1:	movdqa 0xd87(%rip),%xmm2        # 401250 <__dso_handle+0x28>
  4004c9:	nopl   0x0(%rax)
  4004d0:	movdqa %xmm0,%xmm3
  4004d4:	paddb  %xmm1,%xmm3
  4004d8:	movdqu %xmm0,(%r15,%rcx,1)
  4004de:	movdqu %xmm3,0x10(%r15,%rcx,1)
  4004e5:	add    $0x20,%rcx
  4004e9:	paddb  %xmm2,%xmm0
  4004ed:	cmp    %rcx,%rax
  4004f0:	jne    4004d0 <main+0x90>
  4004f2:	cmp    %rax,%r14
  4004f5:	je     400551 <main+0x111>
  4004f7:	test   $0x1c,%r14b
  4004fb:	je     400545 <main+0x105>
  4004fd:	mov    %rax,%rcx
  400500:	mov    %r14,%rax
  400503:	and    $0xfffffffffffffffc,%rax
  400507:	movd   %ecx,%xmm0
  40050b:	punpcklbw %xmm0,%xmm0
  40050f:	pshuflw $0x0,%xmm0,%xmm0
  400514:	por    0xd44(%rip),%xmm0        # 401260 <__dso_handle+0x38>
  40051c:	movdqa 0xd4c(%rip),%xmm1        # 401270 <__dso_handle+0x48>
  400524:	data16 data16 cs nopw 0x0(%rax,%rax,1)
  400530:	movd   %xmm0,(%r15,%rcx,1)
  400536:	add    $0x4,%rcx
  40053a:	paddb  %xmm1,%xmm0
  40053e:	cmp    %rcx,%rax
  400541:	jne    400530 <main+0xf0>
  400543:	jmp    40054c <main+0x10c>
  400545:	mov    %al,(%r15,%rax,1)
  400549:	inc    %rax
  40054c:	cmp    %rax,%r14
  40054f:	jne    400545 <main+0x105>
  400551:	mov    %r15,%rdi
  400554:	mov    %r14,%rsi
  400557:	call   400580 <outer>
  40055c:	mov    %rax,%r14
  40055f:	mov    %r15,%rdi
  400562:	call   400310 <free@plt>
  400567:	mov    $0x401280,%edi
  40056c:	mov    %r14,%rsi
  40056f:	xor    %eax,%eax
  400571:	call   400320 <printf@plt>
  400576:	mov    %ebx,%eax
  400578:	pop    %rbx
  400579:	pop    %r14
  40057b:	pop    %r15
  40057d:	ret
  40057e:	xchg   %ax,%ax

0000000000400580 <outer>:
  400580:	jmp    400590 <middle>
  400582:	data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)

0000000000400590 <middle>:
  400590:	jmp    4005a0 <leaf>
  400592:	data16 data16 data16 data16 cs nopw 0x0(%rax,%rax,1)

00000000004005a0 <leaf>:
  4005a0:	test   %rsi,%rsi
  4005a3:	je     4005c4 <leaf+0x24>
  4005a5:	movabs $0x100000001b3,%rcx
  4005af:	mov    %esi,%edx
  4005b1:	and    $0x3,%edx
  4005b4:	cmp    $0x4,%rsi
  4005b8:	jae    4005ca <leaf+0x2a>
  4005ba:	mov    $0x7,%eax
  4005bf:	xor    %r8d,%r8d
  4005c2:	jmp    40063d <leaf+0x9d>
  4005c4:	mov    $0x7,%eax
  4005c9:	ret
  4005ca:	and    $0xfffffffffffffffc,%rsi
  4005ce:	mov    $0x7,%eax
  4005d3:	xor    %r8d,%r8d
  4005d6:	cs nopw 0x0(%rax,%rax,1)
  4005e0:	movzbl (%rdi,%r8,1),%r9d
  4005e5:	add    %r8,%r9
  4005e8:	add    $0x17,%r9
  4005ec:	xor    %rax,%r9
  4005ef:	imul   %rcx,%r9
  4005f3:	movzbl 0x1(%rdi,%r8,1),%eax
  4005f9:	add    %r8,%rax
  4005fc:	add    $0x18,%rax
  400600:	xor    %r9,%rax
  400603:	imul   %rcx,%rax
  400607:	movzbl 0x2(%rdi,%r8,1),%r9d
  40060d:	add    %r8,%r9
  400610:	add    $0x19,%r9
  400614:	xor    %rax,%r9
  400617:	imul   %rcx,%r9
  40061b:	movzbl 0x3(%rdi,%r8,1),%eax
  400621:	add    %r8,%rax
  400624:	add    $0x1a,%rax
  400628:	xor    %r9,%rax
  40062b:	imul   %rcx,%rax
  40062f:	add    $0x4,%r8
  400633:	cmp    %r8,%rsi
  400636:	jne    4005e0 <leaf+0x40>
  400638:	test   %rdx,%rdx
  40063b:	je     40066b <leaf+0xcb>
  40063d:	add    $0x17,%r8
  400641:	mov    %rax,%rsi
  400644:	data16 data16 cs nopw 0x0(%rax,%rax,1)
  400650:	movzbl -0x17(%rdi,%r8,1),%eax
  400656:	add    %r8,%rax
  400659:	xor    %rsi,%rax
  40065c:	imul   %rcx,%rax
  400660:	inc    %r8
  400663:	mov    %rax,%rsi
  400666:	dec    %rdx
  400669:	jne    400650 <leaf+0xb0>
  40066b:	ret

Disassembly of section .fini:

000000000040066c <_fini>:
  40066c:	endbr64
  400670:	sub    $0x8,%rsp
  400674:	add    $0x8,%rsp
  400678:	ret
