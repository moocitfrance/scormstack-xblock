# Validation

- Python tests cover ZIP/manifest rejection, immutable imports, preserved nested
  asset paths, range requests, OAuth state/issuer/user/PKCE and refresh rotation,
  download boundaries, authorization, publication, progress retries, tab
  conflicts, completion, normalized scores and session duration.
- Tested with Python 3.11.8, Django 4.2.19, XBlock 5.1.0 and web-fragments 2.2.0
  inside an LMS container, against an isolated in-memory SQLite database.
- Also tested on Python 3.12 / Django 5.2 / XBlock 6.3 in an isolated environment.
- Chromium and Firefox smoke tests use two independent player frames. They
  verify failed-save retry and resume after reload, without global window.API
  leakage or uncaught JavaScript errors.
- Django system checks and wheel packaging pass.

Not yet verified: authentication against a real ScormStack deployment, live S3
storage, an enabled component in the actual Studio UI, database-lock contention
under MySQL/PostgreSQL, large production exports, mobile browsers, course
transfer between installations and complete SCORM conformance.
