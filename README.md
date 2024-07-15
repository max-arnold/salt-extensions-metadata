# Salt Extensions Metadata

To leave your comments please visit https://github.com/saltstack/salt/discussions/66144

Use cases:

- Static site generator like Sphinx to build an extension index
- CLI tool that searches it (`salt-pip search`, `spm`?)
- Web based search
- An offline docset generator
- Integration tests (e.g. can all these extensions be installed together?)
- RSS (updates, new extensions, security issues?)
- Potentially can replace https://extensions.saltproject.io/

Metadata file structure:

- One `.yaml` file per extension, stored in the `metadata` folder
- Include/exclude lists stored in the `data` folder (to override the PyPI query automation)

Mandatory fields:

```yaml
name: package-name
repository: https//github.com/user/repo # or https://gitlab.com/user/folder/repo or any other URL
title: A title # can we populate it automatically?
description: Short description
origin: pypi # manual/github/gitlab
```

NOTE: The `origin` field is used to decide what data can be trusted to update some fields automatically

Optional fields:

```yaml
directory: repo directory
categories: [execution, state, engine, cloud, proxy, etc]
official: true # discovered automatically if origin == manual (based on the repo org), otherwise manual
package: https://pypi.org/project/name/  # auto from PyPI or manually from repo
release: 1.0.0 # from PyPI, GH releases, repo tags
release_date: XXXX.XX.XX # from PyPI?, GH releases, repo tags
maintainer: Name <email>
license: XXX # from PyPI or repo
documentation: https://example.com # manually or some heuristics based on PyPI metadata
bugtracker: https://example.com # From repo or guess from PyPI
long_description: Long description
```

To be considered:

```yaml
branch: name # manual override?
salt_version: XXXX.X # from PyPI?
last_commit: XXXX.XX.XX
first_commit: XXXX.XX.XX
first_release: XXXX.XX.XX
issues: NNN
forks: NNN
stars: NNN
```

Scripts:

- `query-pypi.py` (mostly based on [this one](https://github.com/saltstack/salt-extensions-index/blob/main/scripts/query-pypi.py) by Pedro Algarvio) to discover new extensions on PyPI
- `refresh-metadata.py` (TBD) to update the metadata

Search notes:

* `(saltext OR salt-ext OR salt-extension) (path:**/pyproject.toml OR path:**/setup.py OR path:**/setup.cfg)` - Github
* `curl https://api.github.com/users/salt-extensions/repos | jq '.[].name'` - Github
* `(filename:pyproject.toml | filename:setup.cfg | filename:setup.py) + (saltext |  salt-ext | salt-extension)` - doesn't work on Gitlab
* https://pypi.org/project/pkginfo/

Github CI security notes:

* https://securitylab.github.com/research/github-actions-preventing-pwn-requests/
* https://securitylab.github.com/research/github-actions-untrusted-input/
* https://securitylab.github.com/research/github-actions-building-blocks/
* https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions
* https://medium.com/@erez.dasa/github-actions-hardening-guide-faae031aee20
