# ScormStack XBlock

A standalone Open edX component: author in ScormStack, import a **versioned,
self-contained SCORM package** into Studio, then publish the unit through Open
edX. Learner state and grades stay in Open edX. The package files use Django's
configured storage, including a dedicated private S3 bucket.

This is an initial implementation for local evaluation, not a certified general
SCORM player. It has **no dependency on or copied implementation from Overhang's
SCORM XBlock**, and registers as `scormstack` alongside existing `scorm` blocks.

## Implemented

- Studio: account connection/disconnection, paginated course picker, course
  creation, editor link, export with bundled assets, export polling and import.
- Manual trusted ZIP import, usable without a ScormStack account.
- Single-SCO SCORM 1.2 and a practical SCORM 2004 data-model subset; resume,
  suspend data, interactions, score, completion and accumulated session time.
- A separate frame/API per component, local synchronous SCORM reads and a
  sequential asynchronous save queue with retries and visible save failures.
- Encrypted per-author OAuth tokens, authorization code + PKCE S256,
  issuer/user/state validation, rotating refresh tokens and revocation.
- PostgreSQL/MySQL row-locked learner writes with idempotent request sequences.
  Opening a second tab invalidates the previous tab's writing session instead
  of silently overwriting newer answers.
- ZIP path/size checks, safe XML parsing, private file handlers and media range
  responses. A failed import leaves the previous package pointer unchanged.

## Install in Tutor

Install this Python package in **both LMS and CMS**. Open edX automatically
discovers its XBlock and Django app entry points. A migration is required for the
three tables: OAuth connections, pending authorizations and learner attempts.

The package can be installed directly from this repository:

```sh
pip install "scormstack-xblock[s3] @ git+https://github.com/moocitfrance/scormstack-xblock.git@main"
```

Omit `[s3]` when using local storage. Pin a commit instead of `main` for a
reproducible deployment.

For a mounted development checkout, after the services have the directory:

```sh
tutor dev run lms pip install --no-deps -e /openedx/scormstack-xblock
tutor dev run cms pip install --no-deps -e /openedx/scormstack-xblock
tutor dev run lms ./manage.py lms migrate scormstack_xblock
```

Use `/openedx/scormstack-xblock` as the container target for this checkout with
Tutor mounts or a local compose override. Dependencies are declared in
`pyproject.toml`; only use `--no-deps` when they are already present. For a lasting
installation, add the package to the image's Python requirements, rebuild that
image, and restart LMS/CMS. An editable install in a running container alone does
not survive recreation. This repository does not automatically modify Tutor,
rebuild its images, migrate its database, or enable existing courses.

In Studio → Settings → Advanced Settings → Advanced Module List, append
`"scormstack"` without removing existing values. Add it under Advanced in a unit.

## Server configuration

Set `SCORMSTACK` in **both LMS and CMS settings**, for example using a Tutor
`openedx-common-settings` patch. The callback URL is routed only in CMS.

```python
SCORMSTACK = {
    "API_ORIGIN": "https://app.scormstack.io",  # eu.scormstack.io for that deployment
    "STUDIO_ORIGIN": "https://studio.example.org",
    "DOWNLOAD_HOSTS": ["your-export-bucket.s3.eu-west-3.amazonaws.com"],
    "STORAGE_ALIAS": "scormstack",  # omit to use default Django storage
    "STORAGE_PREFIX": "scormstack",
    # Optional stable server secret; defaults to Django SECRET_KEY:
    # "TOKEN_ENCRYPTION_SECRET": os.environ["SCORMSTACK_TOKEN_ENCRYPTION_SECRET"],
}

# Merge this alias into STORAGES; do not replace existing platform aliases.
STORAGES["scormstack"] = {
    "BACKEND": "storages.backends.s3.S3Storage",
    "OPTIONS": {
        "bucket_name": "institution-scorm",
        "region_name": "eu-west-3",
        "default_acl": None,
        "querystring_auth": True,
        # For another S3-compatible provider:
        # "endpoint_url": "https://storage.example.org",
    },
}
```

Use an IAM role or the normal server AWS credential chain. No access keys belong
in XBlock fields, exported course XML, browser responses or this repository.
`DOWNLOAD_HOSTS` is an **exact hostname allowlist for ScormStack's export files**;
it is separate from the destination bucket. Obtain it from your deployment's
export configuration. Downloads do not forward API credentials or follow redirects.
Do not make the destination bucket public: package files are streamed through
authenticated XBlock handlers, preserving relative paths and the SCORM API's
same-origin access. S3 stores both the original ZIP and extracted assets.

Local files work with the platform's default storage. For an explicit local
alias, use `django.core.files.storage.FileSystemStorage` with a writable
`location` shared by LMS and CMS. A private MinIO/S3-compatible backend can also
be configured through Django storages; provider-specific integration still
needs testing.

### Connection and permissions

The plugin registers a public OAuth client with ScormStack and requests only
`courses:read courses:create courses:update`. ScormStack expands course scopes
to the child-content scopes it requires. Publishing uses `courses:update`.
Current ScormStack policy requires an eligible **Pro/Enterprise workspace owner**.
This plugin does not bypass those restrictions or provide automatic SSO into the
editor. The editor uses the author's regular ScormStack browser session.

Registering OAuth requires an HTTPS Studio callback. ScormStack only accepts
plain HTTP redirects for loopback IPs. `ALLOW_LOCAL_HTTP=True` permits localhost,
127.0.0.1 and host.docker.internal for development API requests, but cannot change
the OAuth server's callback policy. Use HTTPS Studio for the normal OAuth flow.

For development or a managed institution, `TOKEN_RESOLVER` may name a callable
`resolve_token(openedx_user_id, api_origin)` returning a per-author server-side
PAT, or None to use OAuth. It must enforce the institution's user/workspace
mapping; do not configure one privileged token for all teachers.

Tokens are encrypted at rest with a key derived from TOKEN_ENCRYPTION_SECRET (or
SECRET_KEY). Changing that secret requires reconnection. Redact callback query
strings at the reverse proxy: OAuth authorization codes must not enter access
logs. Do not enable HTTP debug logging of requests or credentials.

## Publication and learner state

1. Select a course, save its ID, and edit it in ScormStack.
2. Choose **Export and import into Studio**. Export uses SCORM 1.2, embedded assets
   and no ScormStack learner analytics. The browser polls the export status.
3. The server validates and stores a new package under a unique directory.
   Only after success does the draft XBlock reference change.
4. Publish the unit in Open edX to release that package to learners.

Old package directories are deliberately retained: live versions, other course
copies and old attempts may still refer to them. Do not delete them by age alone.
Course XML carries the package reference, **not the binary assets**: moving a
course to a different installation requires copying its referenced storage
directory or re-importing the package. Same-site duplication shares immutable
assets and gives the copied block separate learner state through its usage ID.

Progress is keyed by learner, block usage ID and package SHA-256. Re-importing
identical ZIP bytes resumes the same attempt; a different ZIP starts a separate
state. Old answers remain stored and are not silently applied to new questions.
SCORM scores are normalized to 0–1 and sent through the Open edX grade event;
the subsection still needs normal grading configuration. In SCORM 1.2, failed
is a finished attempt and therefore complete, without implying success. SCORM
2004 completion and success are separate. Studio previews never write results.

## Limits to validate before production

- Only single-SCO packages. Multi-SCO sequencing/navigation is explicitly rejected.
  This is not ADL conformance certification or a complete SCORM 2004 implementation.
- Only trusted author HTML/JavaScript should be imported. Like a same-origin SCORM
  activity, packages are executable content; the iframe isolates API instances,
  not malicious content. Untrusted/public uploads need a separate-origin host and
  a messaging bridge, outside this initial implementation.
- `Commit`/`Finish` accept a save into the asynchronous queue; the visible saved
  status is the server acknowledgement. Autosave and visibility-change flushing
  reduce unload loss. Browser termination cannot guarantee persistence of the
  last unsent changes, especially state above keepalive's size limit.
- Default limits: 256 MiB ZIP/file, 1 GiB extracted, 10,000 files, 1 MiB learner
  state. Override MAX_ZIP_BYTES, MAX_FILE_BYTES, MAX_EXTRACTED_BYTES and MAX_FILES
  in server settings. Configure proxy upload limits/timeouts consistently.
- Export generation runs asynchronously in ScormStack. Download/extraction into
  Studio is currently a bounded request, not a Celery background job. Validate
  large packages against your deployment's request timeout before enabling them.
  Export job metadata uses Django cache with a one-hour TTL; use a shared cache.
- OAuth grants expire according to ScormStack policy; an uncertain refresh is
  not automatically retried. Reconnection may be needed after a lost response.
- Deleting a Django user cascades plugin records. Before production, integrate
  Open edX's anonymization/retirement and data export workflows as appropriate;
  anonymizing an account is not the same as deleting it.
- No native progress/attempt report UI, translations or automatic old-package
  garbage collection in this first version.

## Tests

```sh
python -m venv .venv
.venv/bin/pip install -e '.[test,s3]'
.venv/bin/python -m pytest -q
pnpm install
pnpm test
pnpm exec playwright install chromium firefox
pnpm test:browser
```

Browser tests run against a standalone ephemeral HTTP server, not an existing
course. They verify two simultaneous independent players, a failed-save retry,
score data and resume in Chromium and Firefox. Python tests use an isolated
SQLite database and mock the external API; they do not access production.

See [VALIDATION.md](VALIDATION.md) for the tested dependency versions and
integration scenarios that still need validation.
