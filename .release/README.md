# Release adapter adoption

This directory is intentionally incomplete. Replace every `__REQUIRED_*__`
placeholder with this app's fixed identity, typed operations, evidence paths,
and authoritative local gate. Do not add secrets or executable-selection
fields. Register the consumer described in `broker-consumer-request.json`, then
copy its content-addressed public evidence into `release-adapter.json`.

Offline checks:

```sh
python3 .release/test_release_adapter.py
python3 -m release_tools audit --repository . --adapter .release/release-adapter.json --plan .release/release-plan.json
```

The shared command never runs an app command during audit. A real broker/Xcode
canary remains a separate first-adoption gate.
