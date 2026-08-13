# Merlo standard library

`stdlib/std/` contains the alpha standard-library modules shipped with the compiler. The library is written in Merlo where the language surface is sufficient and delegates host operations through declared effects.

Implemented modules cover the core value types, `Option`, `Result`, text, bytes, collections, console I/O, filesystem paths and streams, CLI arguments, time, randomness, JSON, TCP networking, and HTTP framing.

The standard library follows the language release. No API stability beyond `0.1.0-alpha.1` is promised. Host access remains subject to the calling task's effects and runtime capabilities.
