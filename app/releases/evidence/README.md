# Release evidence

Evidence files contain only public status values, stable identifiers, and
cryptographic hashes. Do not record CloudKit record fields, account identifiers,
certificate private material, tokens, or other secrets.

The signed Development CloudKit probe is human-attended and explicitly opt-in.
Its disposable private zone is `QuizzlerDevelopmentProbe-v1`; it must never be
run against the public database. A missing entitlement or iCloud account is a
visible failure, not a successful skipped probe.
