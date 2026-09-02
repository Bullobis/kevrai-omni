# Security Policy — Kevrai Omni

## TL;DR

Kevrai Omni **never** downloads models or engines from `hf-cdn.sufy.com` or any
non-allowlisted mirror. This is enforced in code, not in the UI, at three
layers (schema parse, Pydantic field validator, runtime URL gate).

## Hard-blocked mirror

The following domain is a typosquat/phishing clone masquerading as a
HuggingFace CDN:

```
hf-cdn.sufy.com
```

Any URL whose hostname matches — or whose path contains — this string is
rejected at every entry point (catalog load, model download, engine install,
custom source). This block is **never** weakened by user toggles; turning
`allow_custom_blocked_mirrors` on in `Settings` permits other known mirrors
but **still blocks `hf-cdn.sufy.com`**.

## Threat model

### Who might attack us?

| Threat actor | Capability | Goal |
|--------------|------------|------|
| Operator of a phishing mirror (`hf-cdn.sufy.com`, etc.) | Controls a download URL | Substitute malicious model weights, exfiltrate hit counts, slip in "uncensored" fine-tunes that bundle trackers |
| Compromised PyPI or GitHub release | Controls an engine-binary host | Slip a backdoored `llama-server`, `mnn.so`, etc. |
| Man-in-the-middle on the network | Can rewrite traffic if TLS validation is off | Steal credentials, swap weights on the fly |
| Untrusted model file | Already downloaded, runs in-process via pickle/torch.load | Arbitrary code execution in the sidecar or engine subprocess |
| Supply-chain attack on the Python deps | Injects code into `pip install` | Inside the sidecar; can read `~/.ssh`, exfiltrate anything |

### What we do about each of them

* **Phishing mirror** — Default-deny at every download entry point, see
  `app/catalog.py:DEFAULT_BLOCKED_MIRRORS` and `app/downloader.py:_check_url`.
* **Compromised engine host** — Engine binaries are fetched ONLY from
  `github.com`, `objects.githubusercontent.com`, `pypi.org`,
  `files.pythonhosted.org`, `mirrors.tencent.com`, `mirrors.aliyun.com`.
  Versions are pinned by URL; SHA-256 is enforced when the engine manifest
  carries one.
* **Network MITM** — All fetches use `httpx` with TLS validation on by
  default. We do not ship any code path that disables cert verification.
* **Untrusted model file** — Models run in their **own subprocess** (e.g.
  `llama-server`) which only reads GGUF bytes; we never call
  `torch.load` / `pickle.load` on user-supplied bytes.
* **Supply-chain on Python deps** — `pip install` is pinned to a mirror the
  maintainer chose (`mirrors.tencent.com` by default), with PyPI as a
  fallback. CI runs the install in a clean venv; tests fail on any
  unexpected dep behaviour.

## What we allow (and only what we allow)

* **Models**: `huggingface.co`, `cdn-lfs.huggingface.co`, `hf-mirror.com` (official HF CN mirror).
* **Engine binaries**: `github.com`, `objects.githubusercontent.com`,
  `pypi.org`, `files.pythonhosted.org`, `mirrors.tencent.com`, `mirrors.aliyun.com`.
* **PyPI index**: defaults to `https://mirrors.tencent.com/pypi/simple/`;
  falls back to `https://pypi.org/simple/`.

User-flipping "allow custom sources" still does **not** permit any blocked
mirror — that list is rooted in `DEFAULT_BLOCKED_MIRRORS` and is meant to
be permanent.

## Defense-in-depth: how the block is enforced

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │ URL submitted in renderer via preload bridge → ipc (api:*) → sidecar │
   └──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │ app.downloader._check_url()      → refuses if scheme ≠ http(s)        │
   │                                  refuses if URL contains a BLOCKED    │
   │                                  refuses if host ∉ ALLOWED_MODEL_HOSTS│
   ├──────────────────────────────────────────────────────────────────────┤
   │ For engines:                                                          │
   │ app.engines.download_zip_engine  → re-checks the URL                  │
   │ app.engines.EngineManager.install → re-checks then streams            │
   ├──────────────────────────────────────────────────────────────────────┤
   │ Pydantic field validators on `ModelEntry.repo` / `gguf_repo`          │
   │ refuse any value containing a BLOCKED substring at CATALOG LOAD TIME  │
   ├──────────────────────────────────────────────────────────────────────┤
   │ catalog/schema.py (jsonschema)        → `_not_blocked_url` per URL    │
   │ catalog._belt_and_suspenders_block() → scans freeform text too       │
   └──────────────────────────────────────────────────────────────────────┘
```

A typo, an override flag, a future PR — none of them can shortcut all
three layers. CI (`tests/test_catalog_schema.py`,
`tests/test_security.py`, `tests/test_no_secrets.py`) verifies each layer
on every push.

## The leaked token

The user posted a GitHub PAT in chat:

```
ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**This token must be considered compromised.** Anyone with access to the
conversation transcript has it. We:

1. Refuse to use it in `scripts/release.sh` (script exits non-zero if
   `GITHUB_TOKEN` starts with the leaked prefix).
2. Scan every file under `catalog/`, `python/`, `electron/`, `scripts/`,
   `docs/`, `renderer/`, `package.json`, etc., and fail CI if the pattern
   reappears anywhere except `SECURITY.md`, `RELEASE.md`,
   `scripts/release.sh`, `scripts/smoke.sh`, `scripts/build_windows.sh`
   (where it appears intentionally to refuse it).
3. Recommend immediate revocation in GitHub Settings → Developer settings →
   Personal access tokens.
4. Recommend regenerating a new token with the minimum required scopes
   (`repo` for `gh release create`; `read:org` if you need org-owned releases).
5. Require the new token to start with `ghp_` or `github_pat_` and be at
   least 40 characters long before the release script will accept it
   (`scripts/release.sh`).

## Reporting a vulnerability

**Preferred (private)**: Email `security@kevrai-studio.example` with PGP
key … (fingerprint `XXXX XXXX XXXX XXXX XXXX  XXXX XXXX XXXX XXXX XXXX`).

**Public**: Open an issue tagged `security` once a fix is shipped.

Please include:
* A reproducible proof of concept (ideally a failing test).
* The affected version (`package.json` field `version`).
* Whether the issue is exploitable against an unprivileged user only, or
  against an attacker who can convince a user to paste a URL.

We follow a 90-day disclosure timeline by default: we'll coordinate with
you to land a fix before the report becomes public, unless immediate
disclosure is necessary to protect users.

## CVE / responsible disclosure

Kevrai Omni follows the
[CVE Numbering Authority](https://www.cve.org/) process. Reported issues
that meet the criteria are assigned a CVE and credited in the release
notes once patched.

## Hard-coded rule for maintainers

* **Never** relax `DEFAULT_BLOCKED_MIRRORS` without a coordinated release
  announcement, a CVE for the prior loophole, and explicit approval from
  the security contact.
* **Never** ship code that calls `pip install`, `git clone`, `curl`, or
  `wget` against a URL not on one of the allowlists above.
* **Never** disable `jsonschema` validation in `app.catalog` to "fix a
  test" — fix the test, not the schema.
