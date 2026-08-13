# Resources

Host resources are explicit closeable values. A `ResourceScope` owns each
resource once and closes owned resources in reverse order on normal and error
exits. Calling a scope after it is closed is rejected; close failures surface as
`ResourceCloseFailure` rather than disappearing.

Resource cleanup is separate from capability authority: a manifest decides
whether an operation is allowed, while a resource scope decides who closes the
result. Keep both contracts visible around filesystem, network, and FFI work.
The alpha runtime is synchronous and does not provide async resource tasks or a
cycle collector.
