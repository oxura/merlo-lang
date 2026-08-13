# Effects and capability contract

The closed alpha effects are:

`console.read`, `console.write`, `fs.read`, `fs.write`, `env.read`, `clock.now`,
`random.read`, `network.tcp`, `network.http`, and `process.args`.

Direct host operations introduce effects and capabilities. The elaborator
propagates them through the private call graph to a fixed point and derives
`fn` for an empty row or `task` otherwise. Postfix `?` propagates a typed error
into a closed, sorted error row. Public interfaces materialize all three rows;
an interface lock never widens during ordinary `check`, `build`, `run`, or
`test`.

A capability manifest must authorize each operation and may constrain
filesystem roots, network hosts, environment keys, and process arguments.
Child scopes can only narrow authority. Missing authority is
`MissingCapability`; a widening attempt is `CapabilityScopeEscape`.

Runtime operations are synchronous. `ResourceScope` closes owned closeable
resources on every exit and reports close failures. These checks scope Merlo
host operations; they are not an operating-system sandbox or a general security
guarantee.
