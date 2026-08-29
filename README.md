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

## Optional Linux SearXNG setup

This section is only for an operator who explicitly wants this optional,
self-hosted acquisition path. Ordinary HAC does not need SearXNG or this
plugin. The SearXNG service remains entirely operator-owned: this package does
not install, configure, start, stop, repair, upgrade, discover, or supervise
it.

The commands below are a narrow path derived from SearXNG's current official
[step-by-step installation guide](https://docs.searxng.org/admin/installation-searxng.html).
For engine choice, maintenance, or a persistent service, follow the official
documentation rather than this integration guide. The integration sequence has
been operator-validated; a clean-machine SearXNG installation has not been
tested by this package.

### 1. Reserve the fixed loopback port

Before configuring SearXNG, check whether its required port is already in use:

```sh
sudo ss -ltnp | grep ':8888'
```

Output means another process is listening on port 8888. The operator must
handle that process before this plugin can work; the plugin endpoint is
intentionally fixed and must not be moved to another port.

### 2. Install SearXNG with the official Linux path

For example, on Ubuntu or Debian, install the prerequisites from the official
guide, then create the dedicated service account and its source/virtualenv
layout:

```sh
sudo -H apt-get install -y \
  python3-dev python3-babel python3-venv python-is-python3 \
  uwsgi uwsgi-plugin-python3 \
  git build-essential libxslt-dev zlib1g-dev libffi-dev libssl-dev

sudo -H useradd --shell /bin/bash --system \
  --home-dir /usr/local/searxng \
  --comment 'Privacy-respecting metasearch engine' \
  searxng
sudo -H mkdir /usr/local/searxng
sudo -H chown -R searxng:searxng /usr/local/searxng

sudo -H -u searxng -i
git clone https://github.com/searxng/searxng /usr/local/searxng/searxng-src
python3 -m venv /usr/local/searxng/searx-pyenv
echo '. /usr/local/searxng/searx-pyenv/bin/activate' >> /usr/local/searxng/.profile
exit

sudo -H -u searxng -i
cd /usr/local/searxng/searxng-src
pip install -U pip setuptools wheel pyyaml msgspec typing-extensions pybind11
pip install --use-pep517 --no-build-isolation -e .
```

### 3. Configure the fixed local contract

Create the official template configuration and replace its placeholder secret:

```sh
sudo -H mkdir -p /etc/searxng
sudo -H cp /usr/local/searxng/searxng-src/utils/templates/etc/searxng/settings.yml \
  /etc/searxng/settings.yml
sudo -H sed -i -e "s/ultrasecretkey/$(openssl rand -hex 16)/g" \
  /etc/searxng/settings.yml
sudoedit /etc/searxng/settings.yml
```

In that file, ensure the `search` and `server` settings include these values
(preserving the rest of the official template):

```yaml
search:
  formats:
    - html
    - json

server:
  port: 8888
  bind_address: "127.0.0.1"
```

`json` is required because the plugin uses SearXNG's JSON Search API. Binding
only to `127.0.0.1:8888` preserves the accepted fixed-loopback contract.

### 4. Start and verify SearXNG first

In the `searxng` user's shell, run the official foreground validation flow:

```sh
cd /usr/local/searxng/searxng-src
export SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml
python -m searx.webapp
```

Keep it running, then use another terminal to verify SearXNG independently of
HAC:

```sh
curl --fail --show-error --silent -X POST \
  --data-urlencode 'q=SearXNG project' \
  -d 'format=json' \
  http://127.0.0.1:8888/search
```

The command should return successfully with a JSON search response. A failure
here is a SearXNG setup problem, not a HAC or plugin problem. This foreground
process is only for validation; persistent service setup is an operator choice
documented by SearXNG, not managed by HAC or this plugin.

## Install the plugin with Home AI Cluster

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

### HAC repository checkout / virtual environment

When HAC runs from a checkout's `.venv`, install this distribution into that
exact environment. Substitute the two checkout paths; no source is copied into
either repository:

```sh
uv pip install \
  --python /path/to/home-ai-cluster/.venv/bin/python \
  /path/to/home-ai-cluster-plugin-searxng
```

The command installs the distribution into HAC's Python environment, not into
the HAC repository tree.

### HAC as an isolated uv tool

Use `uv tool install --with` to create the HAC tool environment with the
published plugin:

```sh
uv tool install --with home-ai-cluster-plugin-searxng home-ai-cluster
```

For an already installed local-development HAC tool snapshot, run this from the
HAC checkout to refresh that tool environment. Here `.` means the local HAC
checkout, not a published package:

```sh
uv tool install --force --no-cache \
  --with home-ai-cluster-plugin-searxng \
  .
```

## Use the optional acquisition path

SearXNG must still be running at `127.0.0.1:8888`, and the separately packaged
plugin must be installed in the same environment as `hac`. Start the ordinary
HAC application separately; it uses its default loopback port, `127.0.0.1:8000`,
not port 8888.

Terminal 1:

```sh
hac local
```

Terminal 2:

```sh
hac external-information \
  --plugin searxng \
  --query "Python 3.14 release notes free threading" \
  --question "What changed for free-threaded Python in 3.14?"
```

External-information acquisition is optional and explicit. Installing this
plugin grants no automatic external-network authority: the operator must make
this one-shot request and explicitly select `searxng` each time.
