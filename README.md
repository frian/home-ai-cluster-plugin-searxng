# Home AI Cluster SearXNG Plugin

This separately installed, explicitly selected external-information acquisition
plugin implements [Home AI Cluster RFC-0079](https://github.com/frian/home-ai-cluster/blob/main/RFC/RFC-0079-fixed-loopback-searxng-acquisition-plugin.md).

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

## Installation with Home AI Cluster

Install this separately packaged Python distribution into the same Python
environment that runs `hac`. HAC discovers it only through
`importlib.metadata.entry_points()` in the accepted
`home_ai_cluster.external_information_acquisition.v1` group.

```text
HAC Python environment
├── home-ai-cluster
└── home-ai-cluster-plugin-searxng
        ↓
    searxng entry point becomes discoverable
```

This does not copy source into the HAC repository, create a HAC `plugins/`
directory, cause filesystem scanning, or add a HAC core dependency. Explicit
`searxng` selection is still required; installation alone does not change
ordinary HAC startup or requests.

The exact command depends on how HAC runs.

### HAC repository checkout

When HAC runs from its `.venv`, install this distribution into that exact
environment:

```sh
uv pip install \
  --python ./home-ai-cluster/.venv/bin/python \
  home-ai-cluster-plugin-searxng
```

For development from a sibling workspace, install the local checkout instead:

```sh
uv pip install --python ./home-ai-cluster/.venv/bin/python ./home-ai-cluster-plugin-searxng
```

The command installs the distribution into HAC's Python environment, not into
the HAC repository tree.

### HAC as an isolated uv tool

Use `uv tool install --with` to create the HAC tool environment with the
published plugin:

```sh
uv tool install --with home-ai-cluster-plugin-searxng home-ai-cluster
```

For development from a sibling workspace, use the local checkout instead:

```sh
uv tool install --with ./home-ai-cluster-plugin-searxng ./home-ai-cluster
```
