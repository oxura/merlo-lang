"""Measured Text/Bytes, recursive-value, and interface scenarios outside the frozen native subset."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .native_bench import _Build, _compile_external
from .stage06p_benchmark import (
    BENCHMARK_SEED,
    _cpu_state,
    _distribution,
    _run_one,
)


EXTENDED_SCENARIO_SCHEMA_VERSION = 2
_LANGUAGES = ("c", "rust", "go", "csharp", "python")
_MASK64 = (1 << 64) - 1


@dataclass(frozen=True)
class ExtendedScenario:
    id: str
    category: str
    input: int
    expected: int


_TEXT_PATTERN = bytes((109, 195, 169, 108, 240, 159, 152, 128, 10))


def _text_checksum(n: int) -> int:
    value = 14695981039346656037
    for index in range(n):
        value ^= _TEXT_PATTERN[index % len(_TEXT_PATTERN)]
        value = (value * 1099511628211) & _MASK64
    return value


def _interface_checksum(n: int) -> int:
    even_count = (n + 1) // 2
    odd_count = n // 2
    even_squares = (
        4
        * (even_count - 1)
        * even_count
        * (2 * even_count - 1)
        // 6
    )
    odd_increments = odd_count * (odd_count + 1)
    return (even_squares + odd_increments) & _MASK64


_INTERFACE_INPUT = 2_000_000
_INTERFACE_EXPECTED = _interface_checksum(_INTERFACE_INPUT)

SCENARIOS = (
    ExtendedScenario("text_bytes_utf8", "text-bytes", 20_000_000, _text_checksum(20_000_000)),
    ExtendedScenario("recursive_values", "recursive-values", 500, (8_386_560 * 500) & _MASK64),
    ExtendedScenario("interface_monomorphic", "interfaces", _INTERFACE_INPUT, _INTERFACE_EXPECTED),
    ExtendedScenario("interface_closed_tag", "interfaces", _INTERFACE_INPUT, _INTERFACE_EXPECTED),
    ExtendedScenario("interface_dispatch", "interfaces", _INTERFACE_INPUT, _INTERFACE_EXPECTED),
)

_UNSUPPORTED_FEATURES = (
    ("Text.valid_utf8", "text_bytes_utf8"),
    ("Text.byte_iteration", "text_bytes_utf8"),
    ("Text.unicode_scalar_iteration", "text_bytes_utf8"),
    ("Text.boundary_checked_slice", "text_bytes_utf8"),
    ("Text.concat_search_builder", "text_bytes_utf8"),
    ("Text.word_count_lines", "text_bytes_utf8"),
    ("Text.integer_parse", "text_bytes_utf8"),
    ("Bytes.safe_slice_copy_transform", "text_bytes_utf8"),
    ("recursive_unique_tree", "recursive_values"),
    ("recursive_linked_list", "recursive_values"),
    ("recursive_enum", "recursive_values"),
    ("shared_acyclic_dag", "recursive_values"),
    ("interface_monomorphic_specialization", "interface_dispatch"),
    ("interface_closed_tag_dispatch", "interface_dispatch"),
    ("interface_dynamic_indirect_call", "interface_dispatch"),
)


def _c_source(scenario: ExtendedScenario) -> str:
    if scenario.id == "text_bytes_utf8":
        run = '''static uint64_t run(uint64_t n) {
    static const unsigned char text[9] = {109,195,169,108,240,159,152,128,10};
    uint64_t checksum = UINT64_C(14695981039346656037);
    for (uint64_t i=0;i<n;++i) { checksum ^= text[i%9]; checksum *= UINT64_C(1099511628211); }
    return checksum;
}'''
    elif scenario.id == "recursive_values":
        run = '''typedef struct Node { uint64_t value; struct Node *left, *right; } Node;
static Node *build(uint64_t value, int depth) {
    if (depth == 0) return NULL;
    Node *node = malloc(sizeof(Node)); if (!node) abort();
    node->value=value; node->left=build(value*2,depth-1); node->right=build(value*2+1,depth-1); return node;
}
static uint64_t fold(const Node *node) { return node ? node->value + fold(node->left) + fold(node->right) : 0; }
static void destroy(Node *node) { if(node){destroy(node->left);destroy(node->right);free(node);} }
static uint64_t run(uint64_t n) { Node *root=build(1,12); uint64_t sum=0; for(uint64_t i=0;i<n;++i)sum+=fold(root); destroy(root); return sum; }'''
    elif scenario.id == "interface_monomorphic":
        run = '''static uint64_t square(uint64_t value){return value*value;}
static uint64_t increment(uint64_t value){return value+1;}
static uint64_t run(uint64_t n){uint64_t sum=0;for(uint64_t i=0;i<n;++i)sum+=(i&1)?increment(i):square(i);return sum;}'''
    elif scenario.id == "interface_closed_tag":
        run = '''typedef enum { SQUARE, INCREMENT } OperationTag;
static uint64_t apply(OperationTag tag,uint64_t value){return tag==SQUARE?value*value:value+1;}
static uint64_t run(uint64_t n){OperationTag operations[2]={SQUARE,INCREMENT};uint64_t sum=0;for(uint64_t i=0;i<n;++i)sum+=apply(operations[i&1],i);return sum;}'''
    else:
        run = '''typedef uint64_t (*Apply)(uint64_t);
typedef struct { Apply apply; } Operation;
static uint64_t square(uint64_t value){return value*value;}
static uint64_t increment(uint64_t value){return value+1;}
static uint64_t run(uint64_t n){Operation operations[2]={{square},{increment}};uint64_t sum=0;for(uint64_t i=0;i<n;++i)sum+=operations[i&1].apply(i);return sum;}'''
    return f'''#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
{run}
int main(int argc,char **argv){{if(argc!=2)return 2;uint64_t n=strtoull(argv[1],NULL,10);printf("%" PRIu64 "\\n",run(n));return 0;}}
'''


def _rust_source(scenario: ExtendedScenario) -> str:
    if scenario.id == "text_bytes_utf8":
        body = '''fn run(n:u64)->u64{let text:[u8;9]=[109,195,169,108,240,159,152,128,10];let mut sum=14695981039346656037u64;for i in 0..n{sum^=text[(i%9)as usize]as u64;sum=sum.wrapping_mul(1099511628211);}sum}'''
    elif scenario.id == "recursive_values":
        body = '''struct Node{value:u64,left:Option<Box<Node>>,right:Option<Box<Node>>}
fn build(value:u64,depth:u32)->Option<Box<Node>>{if depth==0{None}else{Some(Box::new(Node{value,left:build(value*2,depth-1),right:build(value*2+1,depth-1)}))}}
fn fold(node:&Option<Box<Node>>)->u64{match node{None=>0,Some(value)=>value.value.wrapping_add(fold(&value.left)).wrapping_add(fold(&value.right))}}
fn run(n:u64)->u64{let root=build(1,12);let mut sum=0u64;for _ in 0..n{sum=sum.wrapping_add(fold(&root));}sum}'''
    elif scenario.id == "interface_monomorphic":
        body = '''fn square(value:u64)->u64{value.wrapping_mul(value)}fn increment(value:u64)->u64{value.wrapping_add(1)}
fn run(n:u64)->u64{let mut sum=0u64;for i in 0..n{sum=sum.wrapping_add(if i&1==0{square(i)}else{increment(i)});}sum}'''
    elif scenario.id == "interface_closed_tag":
        body = '''enum Operation{Square,Increment}impl Operation{fn apply(&self,value:u64)->u64{match self{Self::Square=>value.wrapping_mul(value),Self::Increment=>value.wrapping_add(1)}}}
fn run(n:u64)->u64{let operations=[Operation::Square,Operation::Increment];let mut sum=0u64;for i in 0..n{sum=sum.wrapping_add(operations[(i&1)as usize].apply(i));}sum}'''
    else:
        body = '''trait Operation{fn apply(&self,value:u64)->u64;}struct Square;struct Increment;impl Operation for Square{fn apply(&self,value:u64)->u64{value.wrapping_mul(value)}}impl Operation for Increment{fn apply(&self,value:u64)->u64{value.wrapping_add(1)}}
fn run(n:u64)->u64{let square=Square;let increment=Increment;let operations:[&dyn Operation;2]=[&square,&increment];let mut sum=0u64;for i in 0..n{sum=sum.wrapping_add(operations[(i&1)as usize].apply(i));}sum}'''
    return body + '\nfn main(){let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap();println!("{}",run(n));}\n'


def _go_source(scenario: ExtendedScenario) -> str:
    if scenario.id == "text_bytes_utf8":
        body = '''func run(n uint64)uint64{text:=[]byte{109,195,169,108,240,159,152,128,10};sum:=uint64(14695981039346656037);for i:=uint64(0);i<n;i++{sum^=uint64(text[i%9]);sum*=1099511628211};return sum}'''
    elif scenario.id == "recursive_values":
        body = '''type Node struct{value uint64;left,right *Node}
func build(value uint64,depth int)*Node{if depth==0{return nil};return &Node{value,build(value*2,depth-1),build(value*2+1,depth-1)}}
func fold(node *Node)uint64{if node==nil{return 0};return node.value+fold(node.left)+fold(node.right)}
func run(n uint64)uint64{root:=build(1,12);var sum uint64;for i:=uint64(0);i<n;i++{sum+=fold(root)};return sum}'''
    elif scenario.id == "interface_monomorphic":
        body = '''func square(v uint64)uint64{return v*v};func increment(v uint64)uint64{return v+1}
func run(n uint64)uint64{var sum uint64;for i:=uint64(0);i<n;i++{if i&1==0{sum+=square(i)}else{sum+=increment(i)}};return sum}'''
    elif scenario.id == "interface_closed_tag":
        body = '''type OperationTag uint8;const(Square OperationTag=iota;Increment);func apply(tag OperationTag,v uint64)uint64{if tag==Square{return v*v};return v+1}
func run(n uint64)uint64{ops:=[2]OperationTag{Square,Increment};var sum uint64;for i:=uint64(0);i<n;i++{sum+=apply(ops[i&1],i)};return sum}'''
    else:
        body = '''type Operation interface{Apply(uint64)uint64};type Square struct{};type Increment struct{};func(Square)Apply(v uint64)uint64{return v*v};func(Increment)Apply(v uint64)uint64{return v+1}
func run(n uint64)uint64{ops:=[2]Operation{Square{},Increment{}};var sum uint64;for i:=uint64(0);i<n;i++{sum+=ops[i&1].Apply(i)};return sum}'''
    return f'''package main
import("fmt";"os";"strconv")
{body}
func main(){{n,e:=strconv.ParseUint(os.Args[1],10,64);if e!=nil{{panic(e)}};fmt.Printf("%d\\n",run(n))}}
'''


def _csharp_source(scenario: ExtendedScenario) -> str:
    if scenario.id == "text_bytes_utf8":
        members = '''static ulong Run(ulong n){byte[] text={109,195,169,108,240,159,152,128,10};ulong sum=14695981039346656037UL;for(ulong i=0;i<n;i++){sum^=text[i%9];sum*=1099511628211;}return sum;}'''
    elif scenario.id == "recursive_values":
        members = '''sealed class Node{public ulong Value;public Node? Left,Right;public Node(ulong v,Node? l,Node? r){Value=v;Left=l;Right=r;}}
static Node? Build(ulong value,int depth)=>depth==0?null:new Node(value,Build(value*2,depth-1),Build(value*2+1,depth-1));
static ulong Fold(Node? node)=>node is null?0:node.Value+Fold(node.Left)+Fold(node.Right);
static ulong Run(ulong n){Node? root=Build(1,12);ulong sum=0;for(ulong i=0;i<n;i++)sum+=Fold(root);return sum;}'''
    elif scenario.id == "interface_monomorphic":
        members = '''static ulong Square(ulong value)=>value*value;static ulong Increment(ulong value)=>value+1;
static ulong Run(ulong n){ulong sum=0;for(ulong i=0;i<n;i++)sum+=(i&1)==0?Square(i):Increment(i);return sum;}'''
    elif scenario.id == "interface_closed_tag":
        members = '''enum OperationTag{Square,Increment}static ulong Apply(OperationTag tag,ulong value)=>tag==OperationTag.Square?value*value:value+1;
static ulong Run(ulong n){OperationTag[] ops={OperationTag.Square,OperationTag.Increment};ulong sum=0;for(ulong i=0;i<n;i++)sum+=Apply(ops[i&1],i);return sum;}'''
    else:
        members = '''interface IOperation{ulong Apply(ulong value);}sealed class Square:IOperation{public ulong Apply(ulong value)=>value*value;}sealed class Increment:IOperation{public ulong Apply(ulong value)=>value+1;}
static ulong Run(ulong n){IOperation[] ops={new Square(),new Increment()};ulong sum=0;for(ulong i=0;i<n;i++)sum+=ops[i&1].Apply(i);return sum;}'''
    return f'''using System;
static class Program{{{members}
static void Main(string[] args){{unchecked{{Console.WriteLine(Run(ulong.Parse(args[0])));}}}}}}
'''


def _python_source(scenario: ExtendedScenario) -> str:
    if scenario.id == "text_bytes_utf8":
        body = '''def run(n):
    text=bytes((109,195,169,108,240,159,152,128,10)); total=14695981039346656037
    for i in range(n): total=((total^text[i%9])*1099511628211)&MASK
    return total'''
    elif scenario.id == "recursive_values":
        body = '''class Node:
    __slots__=("value","left","right")
    def __init__(self,value,left,right): self.value=value;self.left=left;self.right=right
def build(value,depth): return None if depth==0 else Node(value,build(value*2,depth-1),build(value*2+1,depth-1))
def fold(node): return 0 if node is None else (node.value+fold(node.left)+fold(node.right))&MASK
def run(n):
    root=build(1,12);total=0
    for _ in range(n): total=(total+fold(root))&MASK
    return total'''
    elif scenario.id == "interface_monomorphic":
        body = '''def square(value): return value*value
def increment(value): return value+1
def run(n):
    total=0
    for i in range(n): total=(total+(square(i) if i&1==0 else increment(i)))&MASK
    return total'''
    elif scenario.id == "interface_closed_tag":
        body = '''def apply(tag,value): return value*value if tag==0 else value+1
def run(n):
    operations=(0,1);total=0
    for i in range(n): total=(total+apply(operations[i&1],i))&MASK
    return total'''
    else:
        body = '''class Square:
    __slots__=()
    def apply(self,value): return value*value
class Increment:
    __slots__=()
    def apply(self,value): return value+1
def run(n):
    operations=(Square(),Increment());total=0
    for i in range(n): total=(total+operations[i&1].apply(i))&MASK
    return total'''
    return f'''import sys
MASK=(1<<64)-1
{body}
print(run(int(sys.argv[1])))
'''


def _source(language: str, scenario: ExtendedScenario) -> str:
    return {"c": _c_source, "rust": _rust_source, "go": _go_source, "csharp": _csharp_source, "python": _python_source}[language](scenario)


def run_extended_scenarios(
    *,
    output_dir: str | Path = "benchmarks/stage06p_extended",
    repetitions: int = 30,
    warmups: int = 5,
) -> dict[str, Any]:
    if repetitions < 30 or warmups < 1:
        raise ValueError("extended scenarios require 30 measured runs and warmups")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    builds: dict[tuple[str, str], _Build] = {}
    for scenario in SCENARIOS:
        for language in _LANGUAGES:
            directory = root / "corpus" / scenario.id / language
            directory.mkdir(parents=True, exist_ok=True)
            builds[(scenario.id, language)] = _compile_external(
                language,
                _source(language, scenario),
                directory,
                (str(scenario.input),),
            )
    state_before = _cpu_state()
    cpu = state_before["selected_cpu"]
    samples = {
        key: [] for key, build in builds.items() if build.status == "MEASURED"
    }
    rng = random.Random(BENCHMARK_SEED ^ 0x455854)
    schedule_hash = hashlib.sha256()
    for round_index in range(warmups + repetitions):
        schedule = list(samples)
        rng.shuffle(schedule)
        for key in schedule:
            scenario = next(item for item in SCENARIOS if item.id == key[0])
            schedule_hash.update(
                f"{round_index}:{key[0]}:{key[1]}\n".encode()
            )
            result = _run_one(builds[key], scenario.expected, cpu)
            if round_index >= warmups:
                samples[key].append(result)
    state_after = _cpu_state()
    observations = []
    unsupported_reasons = {
        "text_bytes_utf8": "Text/Bytes types absent",
        "recursive_values": "recursive pointer indirection absent",
        "interface_monomorphic": "interface declaration/specialization absent; direct calls are measured in the core benchmark",
        "interface_closed_tag": "closed interface tag lowering absent",
        "interface_dispatch": "closed interfaces absent",
    }
    for scenario in SCENARIOS:
        observations.append(
            {
                "workload": scenario.id,
                "language": "meldra",
                "status": "UNSUPPORTED_DECLARED",
                "correct": None,
                "reason": unsupported_reasons[scenario.id],
            }
        )
        for language in _LANGUAGES:
            values = samples.get((scenario.id, language), [])
            measured = [
                item for item in values if item.get("status") == "MEASURED"
            ]
            wall = [float(item["wall_ms"]) for item in measured]
            rss = [
                float(item["peak_rss_kb"])
                for item in measured
                if item.get("peak_rss_kb")
            ]
            build = builds[(scenario.id, language)]
            observation_seed = BENCHMARK_SEED ^ len(observations)
            observations.append(
                {
                    "workload": scenario.id,
                    "language": language,
                    "status": (
                        "MEASURED"
                        if len(measured) == repetitions
                        else build.status
                    ),
                    "correct": len(measured) == repetitions,
                    "samples": values,
                    "wall_ms": _distribution(wall, seed=observation_seed),
                    "peak_rss_kb": _distribution(
                        rss,
                        seed=observation_seed ^ 0x525353,
                    ),
                    "binary_size": build.binary_size,
                    "compiler": build.compiler,
                    "compiler_version": build.compiler_version,
                }
            )
    c_observations = {
        item["workload"]: item
        for item in observations
        if item["language"] == "c"
    }
    calibration = [
        {
            "workload": scenario.id,
            "reference_language": "c",
            "median_ms": c_observations[scenario.id]["wall_ms"]["median"],
            "target_ms": [200, 500],
            "target_met": (
                c_observations[scenario.id]["wall_ms"]["median"] is not None
                and 200
                <= c_observations[scenario.id]["wall_ms"]["median"]
                <= 500
            ),
        }
        for scenario in SCENARIOS
    ]
    unsupported_features = [
        {
            "feature": feature,
            "workload": workload,
            "status": "UNSUPPORTED_DECLARED",
        }
        for feature, workload in _UNSUPPORTED_FEATURES
    ]
    report = {
        "schema_version": EXTENDED_SCENARIO_SCHEMA_VERSION,
        "kind": "MeldraStage06PExtendedScenarios",
        "protocol": {
            "repetitions": repetitions,
            "warmups": warmups,
            "randomized_order": True,
            "schedule_sha256": schedule_hash.hexdigest(),
            "identical_inputs_checksums": True,
        },
        "environment": {"before": state_before, "after": state_after},
        "scenarios": [item.__dict__ for item in SCENARIOS],
        "observations": observations,
        "calibration": calibration,
        "calibration_target_met": all(
            item["target_met"] for item in calibration
        ),
        "correctness_failures": [
            item for item in observations if item.get("correct") is False
        ],
        "unsupported_features": unsupported_features,
        "meldra_supported_count": 0,
        "meldra_unsupported_category_count": 3,
        "meldra_unsupported_count": len(unsupported_features),
    }
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__=["EXTENDED_SCENARIO_SCHEMA_VERSION","SCENARIOS","run_extended_scenarios"]
