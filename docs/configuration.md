# Configuration

Everything an operator sets lives in `.env` (created by `scripts/setup.sh` from `.env.example`). Compose passes it to the app container. The nginx proxy reads a few more variables from the same file.

## How a value is found

For a setting named `NAME`, the app takes the first of these:

1. the environment variable `LABCIRS_NAME`,
2. the key `NAME` in `labcirs/settings/local_config.json`, if that file exists,
3. the default.

An empty value means "use the default". A setting without a default is required: if it is missing, the app stops at startup with `ImproperlyConfigured` and the name of the variable. The Docker image does not contain `local_config.json` (it is in `.dockerignore`), so Docker installations use `.env` only. The JSON file still works for an installation without Docker.

A value that is valid JSON is decoded: `12345678` becomes a number, `'["a"]'` a list, `true` a boolean. Anything else is text. Write a password that could be mistaken for JSON in JSON quotes, for example `'"12345678"'`.

## Rules for `.env`

- One `KEY=value` per line. No comment after a value.
- JSON values (lists, objects, numbers) go inside single quotes: `LABCIRS_ALLOWED_HOSTS='["cirs.example.org"]'`.
- Yes/no settings accept `true`, `false`, `yes`, `no`, `on`, `off`, `1` and `0`, in any case. Anything else, for example `LABCIRS_DEBUG=maybe`, stops the start with `ImproperlyConfigured`. This is deliberate: a text such as `False` would count as true in many programs, and a mistyped `DEBUG` must never turn the debug pages on.
- Do not commit `.env`. It holds the secret key and the passwords.

## Variables

Defaults are shown in the second column. "required" means the app does not start without it.

### Database (bundled PostgreSQL service `db`)

| Variable | Default | Effect |
|---|---|---|
| `POSTGRES_DB` | `labcirs` | Name of the database. Compose passes it to the `db` service and to the app (`LABCIRS_DB_NAME`). |
| `POSTGRES_USER` | `labcirs` | Database user. Passed to the app as `LABCIRS_DB_USER`. |
| `POSTGRES_PASSWORD` | required | Password of the database user. `docker compose` refuses to start without it. `scripts/setup.sh` fills it. |
| `LABCIRS_DB_PASSWORD` | value of `POSTGRES_PASSWORD` | Password the app uses. Only differs from `POSTGRES_PASSWORD` for an external database. |
| `LABCIRS_DB_HOST` | `db` | Database host as seen from the app container. Set it to use an external server. |
| `LABCIRS_DB_PORT` | `5432` | Port of an external database. |

`compose.yaml` also sets `LABCIRS_DB_ENGINE` (PostgreSQL), `LABCIRS_DB_NAME` and `LABCIRS_DB_USER`. Do not set them in `.env`. The `db` service has no published port; only the app can reach it.

### Proxy (nginx)

| Variable | Default | Effect |
|---|---|---|
| `LABCIRS_SERVER_NAME` | `cirs.example.org` in `.env.example`, `localhost` if unset | Host name of the site. Port 80 redirects to `https://<name>/`. |
| `TLS_CERT_DIR` | `./tls` | Directory with `fullchain.pem` and `privkey.pem`, mounted read-only. Keep it out of git. |
| `BRANDING_DIR` | `./branding` | Directory with logo and theme stylesheet, served under `/branding/`. See [branding.md](branding.md). |
| `HTTP_PORT`, `HTTPS_PORT` | `80`, `443` | Ports published on the host. |
| `APP_UPSTREAM` | `app:8000` | Address of the app as seen from the proxy. |
| `RATE_CODES` | `30r/m` | Limit for code checks (`POST .../search/`). nginx syntax: `r/s` or `r/m`. Burst 10. |
| `RATE_WRITES` | `10r/m` | Limit for new incidents and new comments (`POST`). Burst 5. |
| `RATE_LOGIN` | `10r/m` | Limit for the login pages (`/login/`, `/admin/login/`). Per client address, kept in memory only. Burst 5. |

The limits apply to `POST` requests only. Reading pages is not limited. `RATE_CODES` and `RATE_WRITES` are global: all visitors share one counter, because the proxy does not use client addresses for them. One person who sends many requests can make others wait for a minute. With a handful of reports a week this is acceptable, and it keeps addresses out of the system. At 30 tries a minute, guessing a new code (16 characters, 80 bits) is out of the question. An old 8-character code has about 44 bits, so hitting one particular code takes about 1.3 million years. An attacker who is content with any of N old codes needs 1/N of that, which is still centuries for a few thousand reports.

The proxy accepts request bodies up to 12 MB (photos are limited to 10 MB).

### gunicorn

| Variable | Default | Effect |
|---|---|---|
| `GUNICORN_BIND` | `0.0.0.0:8000` | Address gunicorn listens on inside the app container. |
| `GUNICORN_WORKERS` | `3` | Number of worker processes. |

gunicorn writes no access log.

### Required app settings

| Variable | Effect |
|---|---|
| `LABCIRS_SECRET_KEY` | Secret key of the installation. `scripts/setup.sh` generates it. Never change it on a running system: it invalidates sessions and password reset links. Use a different one for every installation. |
| `LABCIRS_ALLOWED_HOSTS` | Host names the site answers to, for example `'["cirs.example.org"]'`. A request with another `Host` header is refused. |
| `LABCIRS_LANGUAGES` | Languages of the interface as an object, for example `'{"de": "Deutsch", "en": "English"}'`. The first one is the default. A language switch appears in the top bar only when there is more than one. Translations exist for `en` and `de`. |
| `LABCIRS_PARLER_LANGUAGES` | Languages of the content translations (published cases, login info), for example `'["de", "en"]'`. Use the same codes as above. |

### Optional app settings

| Variable | Default | Effect |
|---|---|---|
| `LABCIRS_BEHIND_PROXY` | `false` (`true` in `.env.example`) | Trust `X-Forwarded-Proto` from the proxy. Needed with the bundled nginx, so that Django knows a request came over HTTPS. Turn it on only if a proxy you control sets that header. |
| `LABCIRS_SESSION_COOKIE_SECURE` | `true` | Send the session and CSRF cookies over HTTPS only. Set `false` only for tests without HTTPS. |
| `LABCIRS_CSRF_TRUSTED_ORIGINS` | `[]` | Origins that may send `POST` requests, with scheme, for example `'["https://cirs.example.org:8443"]'`. Needed only when the site is reached on a non-standard port or through another host name. The proxy passes the host name without the port, so a site on port 8443 needs its entry. If an entry is needed and missing, every form is rejected with a 403. |
| `LABCIRS_DEBUG` | `false` | Django's debug pages. Never `true` on a real system: they show settings, code and request data. `production.py` does not force it off, so this variable is the only switch. |
| `LABCIRS_LANGUAGE_CODE` | first language of `LABCIRS_LANGUAGES` | Interface language when the browser sends none. |
| `LABCIRS_PARLER_DEFAULT_LANGUAGE_CODE` | `en` | Language of content translations that is used when one is missing. Must be one of `LABCIRS_PARLER_LANGUAGES`. |
| `LABCIRS_ALL_LANGUAGES_MANDATORY_DEFAULT` | `true` | `true`: all languages of `LABCIRS_PARLER_LANGUAGES` are mandatory for publishing a case. `false`: only the default language. The QM can change this per department in the admin. |
| `LABCIRS_TIME_ZONE` | `UTC` | For example `Europe/Berlin`. |
| `LABCIRS_ROOT_URL` | empty | Path prefix if the site is not served from the root of the host. The bundled proxy serves the root. Leave it empty. |
| `LABCIRS_ORGANIZATION` | `LabCIRS` | Name of the organization: word mark in the top bar (without a logo), logo text for screen readers, footer and page titles. |
| `LABCIRS_SITE_NAME` | `LabCIRS` | Name of the system in page titles, the top bar (next to the logo), the admin and the footer. |
| `LABCIRS_SITE_URL` | empty | Address of the site, for example `https://cirs.example.org`. It appears as plain text (never as a link) at the end of mails to reporters, so that the reader knows where to go. |
| `LABCIRS_ASK_PUBLICATION_CONSENT` | `true` | `true`: the report form asks whether the report may be published after editing. `false`: the form does not ask and every report counts as consented. The QM still decides what is published. |
| `LABCIRS_LOGO_URL` | empty | Logo in the top bar and the admin. Empty: the organization name as text. Must be on the same host, for example `/branding/logo.svg`. |
| `LABCIRS_THEME_CSS_URL` | empty | Extra stylesheet that overrides the colour tokens, for example `/branding/theme.css`. Same host only. |
| `LABCIRS_IMPRINT_URL`, `LABCIRS_PRIVACY_URL` | empty | Footer links "Imprint" and "Privacy". Shown only when set. |
| `LABCIRS_SOURCE_URL` | address of the LabCIRS fork | Footer link "based on LabCIRS (AGPL)". The AGPL requires that users of a modified version can get its source code. If you change the code, point this to your own copy. |

### Mail

Mail is used for three things: password reset for the QM and admins, notifications to the QM about new incidents (switched on per department in the admin) and the optional mails to reporters.

| Variable | Default | Effect |
|---|---|---|
| `LABCIRS_DEFAULT_FROM_EMAIL` | empty | Sender address, for example `cirs@example.org`. Needed for password reset. It also switches on the optional e-mail field for reporters: without a sender address the report form does not offer the field, because a mail could never be sent. |
| `LABCIRS_EMAIL_HOST` | `localhost` | SMTP server. |
| `LABCIRS_EMAIL_PORT` | `25` | SMTP port. |
| `LABCIRS_EMAIL_HOST_USER` | empty | SMTP user. Empty means no login. |
| `LABCIRS_EMAIL_HOST_PASSWORD` | empty | SMTP password. `scripts/setup.sh` asks for it and stores it as a JSON string. |
| `LABCIRS_EMAIL_USE_TLS` | `false` | STARTTLS, usually on port 587. |
| `LABCIRS_EMAIL_USE_SSL` | `false` | Implicit TLS, usually on port 465. Not together with `LABCIRS_EMAIL_USE_TLS`: if both are on, the app refuses to start. |
| `LABCIRS_EMAIL_TIMEOUT` | `10` | Seconds to wait for the mail server. A notification that fails or times out is logged and does not stop the report: the reporter still gets the code. |

To test the mail setup:

```
docker compose run --rm app python manage.py sendtestemail you@example.org
```

Mails to the QM are written in the language set by `LABCIRS_LANGUAGE_CODE`, whatever language the reporter used. Mails to reporters contain only the code, the status, a reply of the QM and `LABCIRS_SITE_URL` as text. Never any field of the report. The reporter's address is kept in its own table, which the admin does not show, and is deleted when the report is closed. Errors while sending are logged by incident number and error class only, because SMTP servers repeat the address in their messages.

LabCIRS sends no error mails. Django's `mail_admins` handler is replaced, so there is no `ADMINS` setting: such mails would carry request data. Errors go to the container log (`docker compose logs app`).

## Fixed in code

These are not settings.

- **Content Security Policy.** The proxy sends `default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'` on every page, including `/admin/`. There is no `unsafe-inline`: the pages use no inline scripts, styles or event handlers. A logo or stylesheet on another host is blocked by the browser.
- **Other headers.** `Strict-Transport-Security: max-age=31536000`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`. The proxy does not send its version (`server_tokens off`).
- **Sessions.** One hour, and they end when the browser closes.
- **Translations.** The texts of published cases and the login info are read from the database on every request. Parler's cache is off: the default cache lives in the memory of one worker, and an edit would stay invisible in the other workers for minutes.
- **Static files.** The app serves them itself through whitenoise, with hashed file names and compression (`CompressedManifestStaticFilesStorage` in production). `collectstatic` runs while the image is built; there is nothing to do at install time. Photos under `/media/` come straight from the proxy.
- **Photos.** JPEG, PNG, GIF and WebP up to 10 MB and 50 megapixels. Every upload is re-encoded from its pixels, so EXIF data (GPS position, camera) and other metadata are dropped, and it is stored under a random name.

## Logs and IP addresses

The system does not log client addresses or user agents: the proxy access log has the format `time "request" status bytes`, gunicorn writes no access log, and the app logs only paths and tracebacks. The one exception is the proxy error log. It is set to level `crit`, where nginx rarely writes a client address, for example when a TLS handshake fails in a particular way. This is accepted. Keep the proxy log (`docker compose logs proxy`) away from people who do not need it, and limit how long Docker keeps container logs (`log-driver` options `max-size` and `max-file` in `/etc/docker/daemon.json`).

## Running without Docker firewall rules (host network)

Some hosts forbid Docker to change the firewall. Then Docker's default bridge network does not work: containers cannot reach each other by name and published ports do not arrive. Run every service on the host network instead. Everything listens on `127.0.0.1` except the proxy, which listens on ports 80 and 443 of the host.

1. Tell the Docker daemon not to touch iptables, in `/etc/docker/daemon.json`, then restart Docker:

   ```json
   { "iptables": false }
   ```

2. Set (or change) in `.env`:

   ```
   APP_UPSTREAM=127.0.0.1:8000
   GUNICORN_BIND=127.0.0.1:8000
   LABCIRS_DB_HOST=127.0.0.1
   LABCIRS_DB_PORT=5433
   ```

3. Create `compose.override.yaml` next to `compose.yaml`. Compose reads it automatically.

   ```yaml
   services:
     db:
       network_mode: host
       # own port and loopback only, so that it does not clash with another PostgreSQL
       command: ["postgres", "-c", "port=5433", "-c", "listen_addresses=127.0.0.1"]
       healthcheck:
         test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -p 5433 -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
     app:
       network_mode: host
       build:
         # without iptables the image build has no route to the package index otherwise
         network: host
     proxy:
       network_mode: host
       ports: !reset []
   ```

4. `docker compose up -d --build` as usual. `HTTP_PORT` and `HTTPS_PORT` have no effect in this mode; the proxy binds 80 and 443.

Check the result with `docker compose config` (shows the merged file) and `docker compose ps`. Make sure nothing else on the host uses 80, 443, 5433 and 8000.
