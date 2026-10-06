# Quick start with Docker

This gets a working LabCIRS running on one Docker host: the app (gunicorn), PostgreSQL 17 and an nginx proxy that terminates TLS. Plan for about 30 minutes.

LabCIRS is for the intranet only. Published cases can be read without a login, and the QM and admin login has no second factor. Do not publish the ports to the internet.

## What you need

- A Linux host (or any machine) with Docker Engine and the Compose plugin, version 2.24 or newer (`docker compose version`).
- `git`, `bash` and `openssl` for `scripts/setup.sh`.
- A host name for the site, for example `cirs.example.org`, and a TLS certificate for it as PEM files (`fullchain.pem` and `privkey.pem`). For a first try you can use a self-signed one, see step 3.
- An SMTP server if you want password reset mails, notifications for the QM or the optional e-mail address for reporters. Without one, everything else works.

## 1. Get the code and create `.env`

```
git clone https://github.com/pbaetz99/labcirs.git
cd labcirs
bash scripts/setup.sh
```

`setup.sh` copies `.env.example` to `.env`, fills `LABCIRS_SECRET_KEY` and the database password with random values, asks for the SMTP password (hidden, Enter skips it) and sets the file mode to 600. It never overwrites a value that is already set, so you can run it again. `.env` is ignored by git; keep it that way.

## 2. Edit `.env`

Open `.env` and set at least these values. [configuration.md](configuration.md) lists all of them.

| Variable | Example |
|---|---|
| `LABCIRS_SERVER_NAME` | `cirs.example.org` |
| `LABCIRS_ALLOWED_HOSTS` | `'["cirs.example.org"]'` |
| `LABCIRS_LANGUAGES` | `'{"de": "Deutsch", "en": "English"}'` (the first language is the default) |
| `LABCIRS_PARLER_LANGUAGES` | `'["de", "en"]'` |
| `LABCIRS_TIME_ZONE` | `Europe/Berlin` |
| `LABCIRS_ORGANIZATION` | the name of your organization |
| `LABCIRS_DEFAULT_FROM_EMAIL` | `cirs@example.org`, plus the `LABCIRS_EMAIL_*` values of your mail server |

JSON values go inside single quotes, with double quotes inside. Do not put a comment after a value. If the site is reached on a port other than 443 or through another host name, also set `LABCIRS_CSRF_TRUSTED_ORIGINS`, for example `'["https://cirs.example.org:8443"]'`.

## 3. Add the certificate

The proxy reads `fullchain.pem` and `privkey.pem` from the directory named in `TLS_CERT_DIR` (default `./tls`). Create it and copy your certificate there: `mkdir -p tls`.

For a test with a self-signed certificate:

```
mkdir -p tls
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=cirs.example.org" \
    -addext "subjectAltName=DNS:cirs.example.org" \
    -keyout tls/privkey.pem -out tls/fullchain.pem
```

Browsers warn about a self-signed certificate. Replace it before real use. The proxy sends `Strict-Transport-Security` for one year, so a browser that has seen the site over HTTPS will refuse plain HTTP for that host name.

## 4. Start

```
docker compose up -d --build
docker compose ps
```

The first build takes a few minutes. The app container runs the database migrations on every start, then starts gunicorn. If a service does not stay up, read its log with `docker compose logs app` (or `db`, `proxy`). A wrong value in `.env` shows up there as an `ImproperlyConfigured` error with the name of the variable.

To check the whole stack end to end on a scratch copy, run `sh scripts/smoke-test.sh`. It starts its own stack on ports 8080 and 8443, checks headers, rate limits, static files and that the logs hold no IP addresses, and removes everything again.

## 5. Create the first accounts

The app migrates the database before it starts gunicorn. Wait until `docker compose logs app` shows `Listening at`, then create a superuser:

```
docker compose run --rm app python manage.py createsuperuser
```

Open `https://cirs.example.org/login/` and log in. The login is only for the QM (reviewers) and admins. Reporters never log in. An admin lands in the admin, the QM on its overview (`/qm/`), where the button "Admin" in the top bar opens the admin.

In the admin (button "Admin" in the top bar):

1. Add a **department**. Its label (letters, numbers, hyphen, underscore) becomes part of the address: `/incidents/<label>/`.
2. The department form needs a **reporter** account. This is a technical account, used only as the author of anonymous comments. Use the green plus next to the field to create the user and the reporter, with a long random password that nobody needs to know.
3. Add the **reviewers** the same way (one account per person in the QM). Reviewers get the permissions they need when they are saved. Give each one an e-mail address, because password reset mails go there.
4. Tick **active**. Reports are accepted only for active departments.
5. Optional: add **organisational units** (the "where did it happen" choices in the report form) under "Organisational units". Reporters see them as a list with one level of sub-units. A unit that is no longer used is set to inactive, not deleted.
6. Optional: open "LabCIRS configuration" for the department to enable e-mail notifications for the QM. This needs a real `LABCIRS_EMAIL_HOST` and recipients with an e-mail address.

With exactly one active department, the start page `/` goes straight to its list of published cases. Reporters report at `/incidents/<label>/create/`; you may want to link that address on your intranet. After sending, they get a code. With the code they open "My report" to see the status and answer the QM.

## Backups

Two things hold data: the database and the photos.

```
docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > labcirs-$(date +%F).dump
docker compose run --rm --no-deps -T app tar -czf - -C /media . > media-$(date +%F).tgz
```

Keep both off the host. A backup is only worth something after you restored it once on a scratch machine. [upgrade.md](upgrade.md) shows the restore commands.

The admin start page can show when the last backup was made and warn when it is overdue. For that, your backup script writes a small status file after each backup that worked, see [configuration.md](configuration.md#system-status-on-the-admin-start-page).

## Update to a newer version of LabCIRS

```
git pull
docker compose up -d --build
```

The new app container migrates the database when it starts. Take a backup first.

## Run the tests

For development, `scripts/dc` runs Compose with `compose.dev.yaml` on top of `compose.yaml`: a test image, no `.env`, the source directory mounted into the container. It uses the same `db` service and `pgdata` volume as the production setup, under the same project name (the name of the directory):

```
sh scripts/dc run --rm app python manage.py test cirs --settings=labcirs.settings.dev
```

`sh scripts/dc up` starts a development server on port 8000 with `DEBUG` on. Do not run `scripts/dc` in a checkout that holds real data: it would start that server against the real database volume. Use a separate clone for development. A clone in another directory has another project name and its own volumes.
