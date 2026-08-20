# Home AI Cluster SearXNG Plugin

This separately installed, explicitly selected external-information acquisition
plugin implements [Home AI Cluster RFC-0079](https://github.com/frian/home-ai-cluster/blob/main/RFC/RFC-0079-fixed-loopback-searxng-acquisition-plugin.md).
It is development software and has not yet been released.

It assumes an operator-owned SearXNG service is already running at
`127.0.0.1:8888` with JSON output enabled. The plugin does not install,
configure, start, stop, or otherwise manage SearXNG.

```text
HAC caller
  -> local SearXNG
  -> operator-configured external search engines
```

The SearXNG result URLs returned by this package are provenance data only. They
are never fetched or otherwise used as network destinations. This first version
has no configurable endpoint.

Installation only makes the `searxng` entry point available for the explicit
Home AI Cluster external-information caller; it does not change ordinary HAC
startup or requests.
