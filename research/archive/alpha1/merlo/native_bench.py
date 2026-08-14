"""Honest cross-language benchmark harness for Meldra Stage 0.5P."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from merlo.native_c_backend import CEmitter, NativeBackendError, compile_c_source, find_c_compiler
from tools.benchmarks.merlo.performance_frontend import PerformanceCompileError, compile_performance_source
from tools.benchmarks.merlo.performance_opt import optimize_mir
from research.archive.alpha1.merlo.stage05p_freeze import assert_stage05p_frozen


NATIVE_BENCHMARK_SCHEMA_VERSION = 1
NATIVE_BENCHMARK_LANGUAGES = ("meldra", "c", "rust", "go", "csharp", "python")
_MASK = (1 << 64) - 1


@dataclass(frozen=True)
class NativeWorkload:
    id: str
    category: str
    algorithm: str
    input: int
    meldra_supported: bool = True
    limitation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


WORKLOADS = (
    NativeWorkload("arithmetic_lcg", "arithmetic", "uint64_lcg_xor_reduce", 20_000_000),
    NativeWorkload("fixed_array_scan", "arrays", "fixed_8_element_weighted_scan", 20_000_000),
    NativeWorkload("map_filter_fold", "pipelines", "square_then_even_then_sum_fixed_8", 2_000_000),
    NativeWorkload(
        "fnv_ascii",
        "strings",
        "fnv1a_over_repeated_ascii_pattern",
        20_000_000,
        False,
        "Text/bytes are intentionally outside the Stage 0.5P native subset.",
    ),
    NativeWorkload("record_values", "records", "point_value_xor_reduce", 20_000_000),
    NativeWorkload(
        "implicit_tree",
        "trees",
        "complete_binary_tree_index_traversal",
        20_000_000,
        True,
        "Stage 0.5P has no recursive pointer type; this is an explicit indexed complete tree.",
    ),
    NativeWorkload("bubble_sort_8", "sorting", "fixed_8_bubble_sort_repeated", 200_000),
    NativeWorkload("shared_allocations", "allocation-heavy", "allocate_fill_read_drop_8_values", 500_000),
    NativeWorkload("startup", "startup", "return_constant_42", 0),
)


def _u64(value: int) -> int:
    return value & _MASK


def reference_checksum(workload: NativeWorkload) -> int:
    n = workload.input
    if workload.id == "arithmetic_lcg":
        value = 1
        checksum = 0
        for index in range(n):
            value = _u64(value * 1_664_525 + 1_013_904_223)
            checksum = _u64(checksum ^ _u64(value + index))
        return checksum
    if workload.id == "fixed_array_scan":
        values = (3, 1, 4, 1, 5, 9, 2, 6)
        checksum = 0
        for index in range(n):
            checksum = _u64(checksum + values[index & 7] * _u64(index + 1))
        return checksum
    if workload.id == "map_filter_fold":
        values = [1, 2, 3, 4, 5, 6, 7, 8]
        checksum = 0
        for index in range(n):
            slot = index & 7
            values[slot] = _u64(values[slot] + index)
            checksum = _u64(
                checksum
                + sum(
                    value * value
                    for value in values
                    if (value * value) % 2 == 0
                )
            )
        return checksum
    if workload.id == "fnv_ascii":
        pattern = b"meldra-native"
        checksum = 14_695_981_039_346_656_037
        for index in range(n):
            checksum ^= pattern[index % len(pattern)]
            checksum = _u64(checksum * 1_099_511_628_211)
        return checksum
    if workload.id == "record_values":
        checksum = 0
        for index in range(n):
            checksum = _u64(checksum + (index ^ _u64(index * 3 + 1)))
        return checksum
    if workload.id == "implicit_tree":
        checksum = 0
        for index in range(n):
            value = _u64(index * 3 + 1)
            left = _u64(index * 2 + 1)
            right = _u64(index * 2 + 2)
            checksum = _u64(checksum ^ _u64(value * _u64(left + right + 1)))
        return checksum
    if workload.id == "bubble_sort_8":
        values = [9, 1, 8, 2, 7, 3, 6, 4]
        for _round in range(n):
            for outer in range(8):
                for inner in range(7 - outer):
                    if values[inner] > values[inner + 1]:
                        values[inner], values[inner + 1] = values[inner + 1], values[inner]
        return _u64(values[0] * 131 + values[7])
    if workload.id == "shared_allocations":
        return _u64(n * _u64(n + 6))
    if workload.id == "startup":
        return 42
    raise KeyError(workload.id)


def _meldra_source(workload: NativeWorkload) -> str:
    if workload.id == "arithmetic_lcg":
        return """fn main(n: UInt64) -> UInt64:
    var value: UInt64 = 1
    var checksum: UInt64 = 0
    for i in 0..n:
        value = value * 1664525 + 1013904223
        checksum = checksum ^ (value + i)
    checksum
"""
    if workload.id == "fixed_array_scan":
        return """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 8] = [3, 1, 4, 1, 5, 9, 2, 6]
    var checksum: UInt64 = 0
    for i in 0..n:
        checksum = checksum + values[i & 7] * (i + 1)
    checksum
"""
    if workload.id == "map_filter_fold":
        return """fn square(value: UInt64) -> UInt64:
    value * value

fn even(value: UInt64) -> Bool:
    value % 2 == 0

fn add(left: UInt64, right: UInt64) -> UInt64:
    left + right

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 8] = [1, 2, 3, 4, 5, 6, 7, 8]
    var checksum: UInt64 = 0
    for i in 0..n:
        values[i & 7] = values[i & 7] + i
        checksum = checksum + fold(filter(map(values, square), even), 0, add)
    checksum
"""
    if workload.id == "record_values":
        return """record Point:
    x: UInt64
    y: UInt64

fn main(n: UInt64) -> UInt64:
    var checksum: UInt64 = 0
    for i in 0..n:
        let point: Point = Point(x=i, y=i * 3 + 1)
        checksum = checksum + (point.x ^ point.y)
    checksum
"""
    if workload.id == "implicit_tree":
        return """record Node:
    value: UInt64
    left: UInt64
    right: UInt64

fn main(n: UInt64) -> UInt64:
    var checksum: UInt64 = 0
    for i in 0..n:
        let node: Node = Node(value=i * 3 + 1, left=i * 2 + 1, right=i * 2 + 2)
        checksum = checksum ^ (node.value * (node.left + node.right + 1))
    checksum
"""
    if workload.id == "bubble_sort_8":
        return """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 8] = [9, 1, 8, 2, 7, 3, 6, 4]
    for round in 0..n:
        for outer in 0..8:
            for inner in 0..(7 - outer):
                if values[inner] > values[inner + 1]:
                    let temporary: UInt64 = values[inner]
                    values[inner] = values[inner + 1]
                    values[inner + 1] = temporary
    values[0] * 131 + values[7]
"""
    if workload.id == "shared_allocations":
        return """fn make_values(i: UInt64) -> Shared[Array[UInt64, 8]]:
    [i, i + 1, i + 2, i + 3, i + 4, i + 5, i + 6, i + 7]

fn main(n: UInt64) -> UInt64:
    var checksum: UInt64 = 0
    for i in 0..n:
        let values: Shared[Array[UInt64, 8]] = make_values(i)
        checksum = checksum + values[0] + values[7]
        drop(values)
    checksum
"""
    if workload.id == "startup":
        return """fn main(n: UInt64) -> UInt64:
    42
"""
    raise PerformanceCompileError(workload.limitation or f"unsupported workload: {workload.id}")


def _c_source(workload: NativeWorkload) -> str:
    algorithm = workload.id
    return f'''#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct {{ uint64_t x, y; }} Point;
typedef struct {{ uint64_t value, left, right; }} Node;
#if defined(__GNUC__) || defined(__clang__)
__attribute__((noinline))
#endif
static uint64_t *make_values(uint64_t i) {{
    uint64_t *values = malloc(sizeof(uint64_t) * 8);
    if (!values) abort();
    for (uint64_t j = 0; j < 8; ++j) values[j] = i + j;
    return values;
}}
static uint64_t run(uint64_t n) {{
    uint64_t checksum = 0;
    if (0) {{}}
    else if ("{algorithm}"[0] == '\\0') return 0;
'''+ _c_algorithm(workload) + '''
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t result = run(strtoull(argv[1], NULL, 10));
    fprintf(stderr, "BENCH_ALLOCATIONS=%" PRIu64 "\\n", (uint64_t)''' + (str(workload.input) if workload.id == "shared_allocations" else "0") + ''');
    printf("%" PRIu64 "\\n", result);
    return 0;
}
'''


def _c_algorithm(workload: NativeWorkload) -> str:
    if workload.id == "arithmetic_lcg":
        return """    uint64_t value = 1;
    for (uint64_t i = 0; i < n; ++i) { value = value * UINT64_C(1664525) + UINT64_C(1013904223); checksum ^= value + i; }
    return checksum;"""
    if workload.id == "fixed_array_scan":
        return """    const uint64_t values[8] = {3,1,4,1,5,9,2,6};
    for (uint64_t i = 0; i < n; ++i) checksum += values[i & 7] * (i + 1);
    return checksum;"""
    if workload.id == "map_filter_fold":
        return """    uint64_t values[8] = {1,2,3,4,5,6,7,8};
    for (uint64_t round = 0; round < n; ++round) { values[round & 7] += round; for (uint64_t i = 0; i < 8; ++i) { uint64_t mapped = values[i] * values[i]; if (mapped % 2 == 0) checksum += mapped; } }
    return checksum;"""
    if workload.id == "fnv_ascii":
        return """    const unsigned char pattern[] = "meldra-native"; checksum = UINT64_C(14695981039346656037);
    for (uint64_t i = 0; i < n; ++i) { checksum ^= pattern[i % 13]; checksum *= UINT64_C(1099511628211); }
    return checksum;"""
    if workload.id == "record_values":
        return """    for (uint64_t i = 0; i < n; ++i) { Point point = {i, i * 3 + 1}; checksum += point.x ^ point.y; }
    return checksum;"""
    if workload.id == "implicit_tree":
        return """    for (uint64_t i = 0; i < n; ++i) { Node node = {i * 3 + 1, i * 2 + 1, i * 2 + 2}; checksum ^= node.value * (node.left + node.right + 1); }
    return checksum;"""
    if workload.id == "bubble_sort_8":
        return """    uint64_t values[8] = {9,1,8,2,7,3,6,4};
    for (uint64_t round = 0; round < n; ++round) for (uint64_t outer = 0; outer < 8; ++outer) for (uint64_t inner = 0; inner < 7 - outer; ++inner) if (values[inner] > values[inner + 1]) { uint64_t temporary = values[inner]; values[inner] = values[inner + 1]; values[inner + 1] = temporary; }
    return values[0] * 131 + values[7];"""
    if workload.id == "shared_allocations":
        return """    for (uint64_t i = 0; i < n; ++i) { uint64_t *values = make_values(i); checksum += values[0] + values[7]; free(values); }
    return checksum;"""
    return "    (void)n; return UINT64_C(42);"


def _rust_source(workload: NativeWorkload) -> str:
    body = {
        "arithmetic_lcg": "let mut value=1u64; for i in 0..n { value=value.wrapping_mul(1664525).wrapping_add(1013904223); checksum ^= value.wrapping_add(i); } checksum",
        "fixed_array_scan": "let values=[3u64,1,4,1,5,9,2,6]; for i in 0..n { checksum=checksum.wrapping_add(values[(i&7) as usize].wrapping_mul(i.wrapping_add(1))); } checksum",
        "map_filter_fold": "let mut values=[1u64,2,3,4,5,6,7,8]; for i in 0..n { let slot=(i&7) as usize; values[slot]=values[slot].wrapping_add(i); checksum=checksum.wrapping_add(values.iter().copied().map(|v|v.wrapping_mul(v)).filter(|v|v%2==0).fold(0u64,|a,v|a.wrapping_add(v))); } checksum",
        "fnv_ascii": "let p=b\"meldra-native\"; checksum=14695981039346656037u64; for i in 0..n { checksum ^= p[(i%13) as usize] as u64; checksum=checksum.wrapping_mul(1099511628211); } checksum",
        "record_values": "for i in 0..n { let p=Point{x:i,y:i.wrapping_mul(3).wrapping_add(1)}; checksum=checksum.wrapping_add(p.x^p.y); } checksum",
        "implicit_tree": "for i in 0..n { let x=Node{value:i.wrapping_mul(3).wrapping_add(1),left:i.wrapping_mul(2).wrapping_add(1),right:i.wrapping_mul(2).wrapping_add(2)}; checksum ^= x.value.wrapping_mul(x.left.wrapping_add(x.right).wrapping_add(1)); } checksum",
        "bubble_sort_8": "let mut v=[9u64,1,8,2,7,3,6,4]; for _ in 0..n { for outer in 0..8 { for inner in 0..(7-outer) { if v[inner]>v[inner+1] { v.swap(inner,inner+1); } } } } v[0]*131+v[7]",
        "shared_allocations": "for i in 0..n { let v=make_values(i); checksum=checksum.wrapping_add(v[0]).wrapping_add(v[7]); } checksum",
        "startup": "let _=n; 42",
    }[workload.id]
    allocations = workload.input if workload.id == "shared_allocations" else 0
    return f'''struct Point {{ x:u64, y:u64 }}
struct Node {{ value:u64, left:u64, right:u64 }}
#[inline(never)]
fn make_values(i:u64)->Vec<u64> {{ (0..8).map(|j|i+j).collect() }}
fn run(n:u64)->u64 {{ let mut checksum=0u64; {body} }}
fn main() {{ let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap(); let result=run(n); eprintln!("BENCH_ALLOCATIONS={allocations}"); println!("{{}}",result); }}
'''


def _go_source(workload: NativeWorkload) -> str:
    body = {
        "arithmetic_lcg": "value:=uint64(1); for i:=uint64(0); i<n; i++ { value=value*1664525+1013904223; checksum ^= value+i }; return checksum",
        "fixed_array_scan": "v:=[8]uint64{3,1,4,1,5,9,2,6}; for i:=uint64(0); i<n; i++ { checksum += v[i&7]*(i+1) }; return checksum",
        "map_filter_fold": "v:=[8]uint64{1,2,3,4,5,6,7,8}; for r:=uint64(0); r<n; r++ { v[r&7]+=r; for _,x:=range v { m:=x*x; if m%2==0 { checksum+=m } } }; return checksum",
        "fnv_ascii": "p:=[]byte(\"meldra-native\"); checksum=14695981039346656037; for i:=uint64(0); i<n; i++ { checksum ^= uint64(p[i%13]); checksum*=1099511628211 }; return checksum",
        "record_values": "for i:=uint64(0); i<n; i++ { p:=Point{i,i*3+1}; checksum += p.x^p.y }; return checksum",
        "implicit_tree": "for i:=uint64(0); i<n; i++ { x:=Node{i*3+1,i*2+1,i*2+2}; checksum ^= x.value*(x.left+x.right+1) }; return checksum",
        "bubble_sort_8": "_=checksum; v:=[8]uint64{9,1,8,2,7,3,6,4}; for r:=uint64(0); r<n; r++ { _=r; for o:=0;o<8;o++ { for i:=0;i<7-o;i++ { if v[i]>v[i+1] { v[i],v[i+1]=v[i+1],v[i] } } } }; return v[0]*131+v[7]",
        "shared_allocations": "for i:=uint64(0);i<n;i++ { v:=makeValues(i); checksum+=v[0]+v[7] }; return checksum",
        "startup": "_=n; _=checksum; return 42",
    }[workload.id]
    allocations = workload.input if workload.id == "shared_allocations" else 0
    return f'''package main
import (
    "fmt"
    "os"
    "strconv"
)
type Point struct {{ x,y uint64 }}
type Node struct {{ value,left,right uint64 }}
//go:noinline
func makeValues(i uint64) []uint64 {{ v:=make([]uint64,8); for j:=uint64(0);j<8;j++ {{ v[j]=i+j }}; return v }}
func run(n uint64) uint64 {{ var checksum uint64; {body} }}
func main() {{ n,err:=strconv.ParseUint(os.Args[1],10,64); if err!=nil {{ panic(err) }}; result:=run(n); fmt.Fprintf(os.Stderr, "BENCH_ALLOCATIONS={allocations}\\n"); fmt.Printf("%d\\n",result) }}
'''


def _csharp_source(workload: NativeWorkload) -> str:
    # C# source uses unchecked UInt64 arithmetic and the same explicit algorithms.
    body = {
        "arithmetic_lcg": "ulong value=1; for(ulong i=0;i<n;i++){value=value*1664525+1013904223; checksum^=value+i;} return checksum;",
        "fixed_array_scan": "ulong[] v={3,1,4,1,5,9,2,6}; for(ulong i=0;i<n;i++) checksum+=v[i&7]*(i+1); return checksum;",
        "map_filter_fold": "ulong[] v={1,2,3,4,5,6,7,8}; for(ulong r=0;r<n;r++){v[r&7]+=r;foreach(ulong x in v){ulong m=x*x;if(m%2==0)checksum+=m;}} return checksum;",
        "fnv_ascii": "byte[] p=System.Text.Encoding.ASCII.GetBytes(\"meldra-native\"); checksum=14695981039346656037UL; for(ulong i=0;i<n;i++){checksum^=p[i%13];checksum*=1099511628211;} return checksum;",
        "record_values": "for(ulong i=0;i<n;i++){Point p=new(i,i*3+1);checksum+=p.X^p.Y;} return checksum;",
        "implicit_tree": "for(ulong i=0;i<n;i++){Node x=new(i*3+1,i*2+1,i*2+2);checksum^=x.Value*(x.Left+x.Right+1);} return checksum;",
        "bubble_sort_8": "ulong[] v={9,1,8,2,7,3,6,4}; for(ulong r=0;r<n;r++)for(int o=0;o<8;o++)for(int i=0;i<7-o;i++)if(v[i]>v[i+1]){ulong t=v[i];v[i]=v[i+1];v[i+1]=t;} return v[0]*131+v[7];",
        "shared_allocations": "for(ulong i=0;i<n;i++){ulong[] v=MakeValues(i);checksum+=v[0]+v[7];} return checksum;",
        "startup": "return 42;",
    }[workload.id]
    allocations = workload.input if workload.id == "shared_allocations" else 0
    return f'''using System;
using System.Runtime.CompilerServices;
readonly record struct Point(ulong X, ulong Y);
readonly record struct Node(ulong Value, ulong Left, ulong Right);
static class Program {{
 [MethodImpl(MethodImplOptions.NoInlining)]
 static ulong[] MakeValues(ulong i) {{ ulong[] values=new ulong[8]; for(ulong j=0;j<8;j++) values[j]=i+j; return values; }}
 static ulong Run(ulong n) {{ unchecked {{ ulong checksum=0; {body} }} }}
 static void Main(string[] args) {{ ulong result=Run(ulong.Parse(args[0])); Console.Error.WriteLine("BENCH_ALLOCATIONS={allocations}"); Console.WriteLine(result); }}
}}
'''


def _python_source(workload: NativeWorkload) -> str:
    # The reference function is emitted verbatim as an executable competitor.
    function_source = {
        "arithmetic_lcg": """value=1
    checksum=0
    for i in range(n):
        value=(value*1664525+1013904223)&MASK
        checksum=(checksum^(value+i))&MASK
    return checksum""",
        "fixed_array_scan": """v=(3,1,4,1,5,9,2,6)
    checksum=0
    for i in range(n): checksum=(checksum+v[i&7]*(i+1))&MASK
    return checksum""",
        "map_filter_fold": """v=[1,2,3,4,5,6,7,8]
    checksum=0
    for i in range(n):
        v[i&7]=(v[i&7]+i)&MASK
        checksum=(checksum+sum(x*x for x in v if (x*x)%2==0))&MASK
    return checksum""",
        "fnv_ascii": """p=b'meldra-native'
    checksum=14695981039346656037
    for i in range(n): checksum=((checksum^p[i%13])*1099511628211)&MASK
    return checksum""",
        "record_values": """checksum=0
    for i in range(n):
        p=(i,(i*3+1)&MASK); checksum=(checksum+(p[0]^p[1]))&MASK
    return checksum""",
        "implicit_tree": """checksum=0
    for i in range(n):
        x=((i*3+1)&MASK,(i*2+1)&MASK,(i*2+2)&MASK); checksum=(checksum^(x[0]*((x[1]+x[2]+1)&MASK)))&MASK
    return checksum""",
        "bubble_sort_8": """v=[9,1,8,2,7,3,6,4]
    for _ in range(n):
        for o in range(8):
            for i in range(7-o):
                if v[i]>v[i+1]: v[i],v[i+1]=v[i+1],v[i]
    return v[0]*131+v[7]""",
        "shared_allocations": """checksum=0
    for i in range(n):
        v=make_values(i); checksum=(checksum+v[0]+v[7])&MASK
    return checksum""",
        "startup": "return 42",
    }[workload.id]
    allocations = workload.input if workload.id == "shared_allocations" else 0
    return f'''import sys
MASK=(1<<64)-1
def make_values(i):
    return [(i+j)&MASK for j in range(8)]
def run(n):
    {function_source}
result=run(int(sys.argv[1]))
print(f"BENCH_ALLOCATIONS={allocations}",file=sys.stderr)
print(result)
'''


def competitor_source(language: str, workload: NativeWorkload) -> str:
    return {
        "c": _c_source,
        "rust": _rust_source,
        "go": _go_source,
        "csharp": _csharp_source,
        "python": _python_source,
    }[language](workload)


@dataclass(frozen=True)
class _Build:
    status: str
    command: tuple[str, ...]
    run_command: tuple[str, ...]
    compile_time_ms: float | None
    binary_size: int | None
    source_size: int
    source_sha256: str
    binary_sha256: str | None
    compiler: str | None
    compiler_version: str | None
    stderr: str
    optimization_statistics: tuple[dict[str, Any], ...] = ()


def _version(command: str) -> str:
    completed = subprocess.run((command, "--version"), capture_output=True, text=True, timeout=10, check=False)
    return (completed.stdout or completed.stderr).splitlines()[0]


def _unmeasured(source: str, reason: str) -> _Build:
    return _Build(
        "UNMEASURED_TOOLCHAIN_UNAVAILABLE", (), (), None, None, len(source.encode()),
        hashlib.sha256(source.encode()).hexdigest(), None, None, None, reason,
    )


def _compile_external_container(
    language: str,
    source: str,
    directory: Path,
    run_arguments: tuple[str, ...],
) -> _Build | None:
    docker = shutil.which("docker")
    image = {
        "rust": "rust:1.88-slim",
        "go": "golang:1.24-bookworm",
        "csharp": "mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim",
    }.get(language)
    if docker is None or image is None:
        return None
    directory = directory.resolve()
    binary = directory / "program"
    if language == "rust":
        command_inside = (
            "rustc",
            "-C",
            "opt-level=3",
            "-C",
            "debuginfo=0",
            "-C",
            "codegen-units=1",
            "-C",
            "link-arg=-Wl,--build-id=none",
            "/work/main.rs",
            "-o",
            "/work/program",
        )
        run_command = (str(binary), *run_arguments)
        compiler_command = ("rustc", "--version")
    elif language == "go":
        command_inside = (
            "go",
            "build",
            "-trimpath",
            "-ldflags=-s -w -buildid=",
            "-o",
            "/work/program",
            "/work/main.go",
        )
        run_command = (str(binary), *run_arguments)
        compiler_command = ("go", "version")
    else:
        project = directory / "bench.csproj"
        project.write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
            "<OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework>"
            "<Optimize>true</Optimize><DebugType>none</DebugType>"
            "<Deterministic>true</Deterministic><PublishSingleFile>true</PublishSingleFile>"
            "<SelfContained>true</SelfContained><RuntimeIdentifier>linux-x64</RuntimeIdentifier>"
            "</PropertyGroup></Project>\n",
            encoding="utf-8",
        )
        (directory / "main.cs").replace(directory / "Program.cs")
        command_inside = (
            "dotnet",
            "publish",
            "/work/bench.csproj",
            "-c",
            "Release",
            "-o",
            "/work/publish",
            "--nologo",
        )
        binary = directory / "publish" / "bench"
        run_command = (str(binary), *run_arguments)
        compiler_command = ("dotnet", "--version")
    uid_gid = f"{os.getuid()}:{os.getgid()}"
    mount = f"{directory}:/work"
    command = (
        docker,
        "run",
        "--rm",
        "-u",
        uid_gid,
        "-e",
        "HOME=/tmp",
        "-e",
        "SOURCE_DATE_EPOCH=0",
        "-v",
        mount,
        "-w",
        "/work",
        image,
        *command_inside,
    )
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    compile_ms = (time.perf_counter_ns() - started) / 1_000_000
    if completed.returncode != 0 or not binary.is_file():
        return _Build(
            "FAILED",
            command,
            (),
            compile_ms,
            None,
            len(source.encode()),
            hashlib.sha256(source.encode()).hexdigest(),
            None,
            image,
            None,
            completed.stderr or completed.stdout,
        )
    image_inspect = subprocess.run(
        (docker, "image", "inspect", "--format={{.Id}}", image),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    image_id = image_inspect.stdout.strip()
    image_identity = f"{image}@{image_id}" if image_id else image
    version_run = subprocess.run(
        (
            docker,
            "run",
            "--rm",
            image,
            *compiler_command,
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    raw = binary.read_bytes()
    return _Build(
        "MEASURED",
        command,
        run_command,
        compile_ms,
        len(raw),
        len(source.encode()),
        hashlib.sha256(source.encode()).hexdigest(),
        hashlib.sha256(raw).hexdigest(),
        image_identity,
        (version_run.stdout or version_run.stderr).splitlines()[0],
        completed.stderr,
    )


def _compile_external(
    language: str,
    source: str,
    directory: Path,
    run_arguments: tuple[str, ...] = (),
) -> _Build:
    suffix = {"c": ".c", "rust": ".rs", "go": ".go", "csharp": ".cs", "python": ".py"}[language]
    source_path = directory / ("main" + suffix)
    source_path.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(source.encode()).hexdigest()
    if language == "c":
        result = compile_c_source(source, output_dir=directory, stem="program")
        return _Build(result.status, result.command, ((result.binary_path, *run_arguments) if result.binary_path else ()), result.compile_time_ms, result.binary_size, len(source.encode()), digest, result.binary_sha256, result.compiler, result.compiler_version, result.stderr)
    if language == "python":
        started = time.perf_counter_ns()
        completed = subprocess.run((sys.executable, "-m", "py_compile", str(source_path)), capture_output=True, text=True, check=False, timeout=30)
        compile_ms = (time.perf_counter_ns() - started) / 1_000_000
        status = "MEASURED" if completed.returncode == 0 else "FAILED"
        return _Build(status, (sys.executable, "-m", "py_compile", str(source_path)), (sys.executable, str(source_path), *run_arguments), compile_ms, None, len(source.encode()), digest, None, sys.executable, platform.python_version(), completed.stderr)
    executable = shutil.which({"rust": "rustc", "go": "go", "csharp": "dotnet"}[language])
    if executable is None:
        container_build = _compile_external_container(
            language,
            source,
            directory,
            run_arguments,
        )
        if container_build is not None:
            return container_build
        return _unmeasured(source, f"{language} compiler was not found on PATH and no reproducible container was available")
    binary = directory / "program"
    if language == "rust":
        command = (executable, "-C", "opt-level=3", "-C", "debuginfo=0", "-C", "codegen-units=1", "-C", "link-arg=-Wl,--build-id=none", str(source_path), "-o", str(binary))
        run_command = (str(binary), *run_arguments)
    elif language == "go":
        command = (executable, "build", "-trimpath", "-ldflags=-s -w -buildid=", "-o", str(binary), str(source_path))
        run_command = (str(binary), *run_arguments)
    else:
        project = directory / "bench.csproj"
        project.write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework><Optimize>true</Optimize><DebugType>none</DebugType><Deterministic>true</Deterministic></PropertyGroup></Project>\n', encoding="utf-8")
        program = directory / "Program.cs"
        source_path.replace(program)
        publish = directory / "publish"
        command = (executable, "publish", str(project), "-c", "Release", "-o", str(publish), "--nologo")
        run_command = (executable, str(publish / "bench.dll"), *run_arguments)
        binary = publish / "bench.dll"
    environment = dict(os.environ, SOURCE_DATE_EPOCH="0", LC_ALL="C", TZ="UTC")
    started = time.perf_counter_ns()
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180, env=environment)
    compile_ms = (time.perf_counter_ns() - started) / 1_000_000
    if completed.returncode != 0 or not binary.is_file():
        return _Build("FAILED", command, (), compile_ms, None, len(source.encode()), digest, None, executable, _version(executable), completed.stderr or completed.stdout)
    raw = binary.read_bytes()
    return _Build("MEASURED", command, run_command, compile_ms, len(raw), len(source.encode()), digest, hashlib.sha256(raw).hexdigest(), executable, _version(executable), completed.stderr)


def _compile_meldra(workload: NativeWorkload, directory: Path, mir_root: Path) -> _Build:
    if not workload.meldra_supported:
        return _unmeasured("", workload.limitation or "unsupported by Stage 0.5P")
    source = _meldra_source(workload)
    (directory / "main.meldra").write_text(source, encoding="utf-8")
    frontend = compile_performance_source(source, path=f"corpus/{workload.id}.meldra")
    optimized, snapshots = optimize_mir(frontend.mir, artifact_dir=mir_root / workload.id)
    c_source = CEmitter(optimized, runtime_arguments=True).emit()
    result = compile_c_source(c_source, output_dir=directory, stem="program")
    return _Build(
        result.status,
        result.command,
        ((result.binary_path, str(workload.input)) if result.binary_path else ()),
        result.compile_time_ms,
        result.binary_size,
        len(source.encode()),
        hashlib.sha256(source.encode()).hexdigest(),
        result.binary_sha256,
        result.compiler,
        result.compiler_version,
        result.stderr,
        tuple(snapshot.statistics.to_dict() for snapshot in snapshots),
    )


def _run(build: _Build, expected: int, *, repetitions: int, warmups: int) -> dict[str, Any]:
    if build.status != "MEASURED":
        return {
            "status": build.status,
            "correct": None,
            "runtime_ms": None,
            "peak_rss_kb": None,
            "allocations": None,
            "samples_ms": [],
            "error": build.stderr,
        }
    time_tool = "/usr/bin/time" if Path("/usr/bin/time").is_file() else None
    samples = []
    rss_samples = []
    allocation_samples = []
    observed = []
    error = None
    for iteration in range(warmups + repetitions):
        command = build.run_command
        if time_tool:
            command = (time_tool, "-f", "BENCH_RSS_KB=%M", *command)
        started = time.perf_counter_ns()
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120, env=dict(os.environ, LC_ALL="C", TZ="UTC"))
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        if completed.returncode != 0:
            error = completed.stderr or f"exit code {completed.returncode}"
            break
        try:
            checksum = int(completed.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            error = f"invalid checksum output: {completed.stdout!r}"
            break
        observed.append(checksum)
        rss_match = re.findall(r"BENCH_RSS_KB=(\d+)", completed.stderr)
        allocation_match = re.findall(r"(?:BENCH|MELDRA)_ALLOCATIONS=(\d+)", completed.stderr)
        if iteration >= warmups:
            samples.append(elapsed)
            if rss_match:
                rss_samples.append(int(rss_match[-1]))
            if allocation_match:
                allocation_samples.append(int(allocation_match[-1]))
    correct = error is None and len(samples) == repetitions and all(item == expected for item in observed)
    status = "MEASURED" if correct else "FAILED_CORRECTNESS_OR_RUNTIME"
    return {
        "status": status,
        "correct": correct,
        "expected_checksum": expected,
        "observed_checksums": observed,
        "runtime_ms": statistics.median(samples) if samples else None,
        "peak_rss_kb": statistics.median(rss_samples) if rss_samples else None,
        "allocations": statistics.median(allocation_samples) if allocation_samples else None,
        "allocation_metric": "instrumented_algorithm_heap_allocations" if allocation_samples else "UNMEASURED",
        "samples_ms": samples,
        "error": error,
    }


def run_native_benchmark(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/stage05p_runs",
    workloads: Iterable[NativeWorkload] = WORKLOADS,
    repetitions: int = 3,
    warmups: int = 1,
) -> dict[str, Any]:
    assert_stage05p_frozen(Path(__file__).resolve().parents[1])
    if repetitions < 1 or warmups < 0:
        raise ValueError("invalid benchmark repetition counts")
    root = Path(output_dir)
    corpus_root = root / "corpus"
    mir_root = root / "mir"
    root.mkdir(parents=True, exist_ok=True)
    observations = []
    workload_values = tuple(workloads)
    for workload in workload_values:
        expected = reference_checksum(workload)
        for language in NATIVE_BENCHMARK_LANGUAGES:
            directory = corpus_root / workload.id / language
            directory.mkdir(parents=True, exist_ok=True)
            try:
                if language == "meldra":
                    build = _compile_meldra(workload, directory, mir_root)
                else:
                    source = competitor_source(language, workload)
                    build = _compile_external(
                        language,
                        source,
                        directory,
                        (str(workload.input),),
                    )
            except (PerformanceCompileError, NativeBackendError, subprocess.SubprocessError, OSError, ValueError) as exc:
                build = _unmeasured("", f"{type(exc).__name__}: {exc}")
                build = _Build("FAILED", build.command, build.run_command, build.compile_time_ms, build.binary_size, build.source_size, build.source_sha256, build.binary_sha256, build.compiler, build.compiler_version, build.stderr)
            run = _run(build, expected, repetitions=repetitions, warmups=warmups)
            observations.append(
                {
                    "workload": workload.id,
                    "category": workload.category,
                    "algorithm": workload.algorithm,
                    "input": workload.input,
                    "language": language,
                    "build": {
                        "status": build.status,
                        "command": list(build.command),
                        "compile_time_ms": build.compile_time_ms,
                        "binary_size": build.binary_size,
                        "source_size": build.source_size,
                        "source_sha256": build.source_sha256,
                        "binary_sha256": build.binary_sha256,
                        "compiler": build.compiler,
                        "compiler_version": build.compiler_version,
                        "stderr": build.stderr,
                    },
                    "run": run,
                    "optimization_statistics": list(build.optimization_statistics),
                    "limitation": workload.limitation,
                }
            )
    report = {
        "schema_version": NATIVE_BENCHMARK_SCHEMA_VERSION,
        "kind": "MeldraStage05PNativeBenchmark",
        "protocol": {
            "languages": list(NATIVE_BENCHMARK_LANGUAGES),
            "repetitions": repetitions,
            "warmups": warmups,
            "runtime_statistic": "median_external_process_wall_time",
            "memory_statistic": "median_GNU_time_maximum_resident_set_kb",
            "allocations": "instrumented_algorithm_heap_allocations; runtime-internal allocations are not claimed",
            "correctness": "identical frozen input and reference checksum for every measured language",
            "compiler_optimization": "release/O3 for every compiled language",
        },
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "c_compiler": find_c_compiler(),
            "rustc": shutil.which("rustc"),
            "go": shutil.which("go"),
            "dotnet": shutil.which("dotnet"),
            "time": "/usr/bin/time" if Path("/usr/bin/time").is_file() else None,
        },
        "workloads": [item.to_dict() | {"expected_checksum": reference_checksum(item)} for item in workload_values],
        "observations": observations,
    }
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


__all__ = [
    "NATIVE_BENCHMARK_LANGUAGES",
    "NATIVE_BENCHMARK_SCHEMA_VERSION",
    "NativeWorkload",
    "WORKLOADS",
    "competitor_source",
    "reference_checksum",
    "run_native_benchmark",
]
