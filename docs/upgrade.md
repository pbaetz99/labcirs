# Upgrading from LabCIRS v5, v6 or v7

This page moves an existing installation to this version: PostgreSQL 17, Python 3.13, Django 5.2, running in Docker. The data moves as a database dump and a copy of the photos. Nothing runs in place, so the old system stays untouched until you switch.

The path from v5.2.1 is tested end to end by `scripts/test-upgrade-from-v5.sh`, on synthetic data. It builds a v5.2.1 installation (Python 2.7, Django 1.11, PostgreSQL 11) in containers, dumps it, restores the dump into PostgreSQL 17 and runs the steps below. See "Rehearse first". A v6.0 or v7.0 database is already at the last migration of the old line, so it has fewer steps to run. That path is not tested separately.

The new stack uses PostgreSQL only. If the old system ran on MySQL or SQLite, move the data to PostgreSQL with the old version first. This is not covered here.

## What changes for people

- Reporters no longer log in. They report at `/incidents/<label>/create/` and get a code. The reporter account of each department stays in the database as the author of anonymous comments, and it can no longer log in.
- Old 8-character codes keep working. New codes have 16 characters.
- QM and admins keep their accounts and passwords. Everyone has to log in again once, because old sessions are not valid any more.
- Self-registration of new departments is gone. Accounts that registered but were never activated stay in the user list. Delete them in the admin if you do not need them.
- Photos get new random file names and lose their metadata (see step 7). Addresses of old photo files stop working.
- Apache and `mod_wsgi` are replaced by the proxy container. Old Apache files are not needed.

## Settings

Settings move from `labcirs/settings/local_config.json` to `.env`. Every key keeps its name; add the prefix `LABCIRS_`: `DB_HOST` becomes `LABCIRS_DB_HOST`, `LANGUAGES` becomes `LABCIRS_LANGUAGES`, and so on. [configuration.md](configuration.md) has the list. The keys `REGISTRATION_*`, `ACCOUNT_ACTIVATION_DAYS` and `ADMINS` are gone and are ignored if still present. Database settings are handled by `compose.yaml` and the `POSTGRES_*` variables.

Use the same `LANGUAGES` and `PARLER_LANGUAGES` as before. The mandatory languages of each department are stored in the database and move with it.

## Steps

### Before you start

- Read the old counts and write them down. They are the acceptance test in step 9. Run this on the old database:

  ```
  psql -U labcirs -d labcirs -c "select (select count(*) from cirs_department) as departments, (select count(*) from cirs_criticalincident) as incidents, (select count(*) from cirs_publishableincident where publish) as published, (select count(*) from cirs_comment) as comments, (select count(*) from auth_user) as users;"
  ```

- Plan a window in which no one reports. Reports made on the old system after the dump are not in the new one.
- Keep the old system as it is, switched off but not deleted, until the new one is accepted. Keep the dump and the media archive of step 2 as well. They are the way back.

### 1. Prepare the new host and start only the database

Follow steps 1 to 3 of [quickstart.md](quickstart.md): clone, `bash scripts/setup.sh`, edit `.env`, add the certificate. Then start the database and nothing else:

```
docker compose up -d --wait db
```

Do not start the app yet. Its first start creates the tables, and the restore needs an empty database. If it happened anyway, run `docker compose down -v` (this deletes the new, still empty, volumes) and start again.

### 2. Dump the old database and archive the photos

Stop the old application. Then on the old host:

```
pg_dump -Fc -U labcirs -f labcirs-old.dump labcirs
tar -czf media-old.tgz -C /opt/labcirs media
```

Use the custom format (`-Fc`): `pg_restore` needs it. The photos are in the `media` directory next to the old checkout (`MEDIA_ROOT` of the old settings is the parent of the project directory). Copy both files to the new host. The dump holds every report in clear text. Copy it encrypted, and not through chat or mail.

### 3. Restore

```
docker compose exec -T db sh -c 'pg_restore --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < labcirs-old.dump
```

`--no-owner` is needed because the role that owned the old tables does not exist here. A successful restore prints nothing.

### 4. Migrate

```
docker compose run --rm app python manage.py migrate
```

From v5.2.1 this applies `admin.0003`, `auth.0009` to `0012` and `cirs.0018` to `0025`. Django sees that the old migrations `0001` to `0017` are the first part of the squashed migration `0001_squashed_0019_reinitialized` and marks it as applied. The new migrations move the primary keys to `bigint`, add the organisational units, the reporter contact table and the new permissions for existing reviewers, and remove the tables, content types and permissions of the old registration app (`0020`, `0025`). In the test, with three reports, it took about ten seconds. The change to `bigint` rewrites the tables, so allow more time for a large database.

### 5. Remove stale content types

```
docker compose run --rm app python manage.py remove_stale_contenttypes --noinput
```

This removes content types of models that no longer exist in an installed app. It prints nothing when there is nothing to remove, which is the usual case here. The content types of the registration app are removed by migration `0025`, because this command does not look at apps that are no longer installed.

### 6. Copy the photos

Unpack the archive into a directory and mount it read-only into a one-off container as root, which copies it into the `media` volume and hands it to the app user:

```
mkdir media-old && tar -xzf media-old.tgz -C media-old
docker compose run --rm --user root -v "$PWD/media-old/media:/old:ro" app sh -c 'cp -a /old/. /media/ && chown -R labcirs /media'
```

The app runs as the user `labcirs` (uid 10001), not as root. It must own the files, or step 7 cannot replace them. Check afterwards that `docker compose run --rm app ls /media/photos` lists your year directories.

### 7. Clean the photos

```
docker compose run --rm app python manage.py strip_photo_metadata
```

For every photo it writes a clean copy (JPEG stays JPEG, everything else becomes PNG) under a random name in the same folder, updates the database and removes the old file. It prints one line per photo and a summary:

```
cleaned photos/2019/05/03/IMG_2231.jpg -> photos/2019/05/03/804acfabaaa84743a949d93f7eff225c.jpg
already clean photos/2026/10/01/804acfabaaa84743a949d93f7eff225c.jpg
failed photos/2018/01/09/scan.png: The photo has more than 50 megapixels.
1 cleaned, 1 already clean, 1 failed
```

The old file names can identify people. The output goes to your terminal only. Do not paste it into tickets or logs.

The exit status is 1 if any photo failed or an old file could not be removed. In the second case the summary ends with `, N old file(s) not removed`. Two kinds of lines need your hand, so read the output either way:

- **`failed <name>: <reason>`** The reason is `cannot identify image file ...` (the file is not a readable image), `No such file or directory` (the file is not in the volume: check step 6) or `The photo has more than 50 megapixels.` The photo keeps its metadata and its old name, and the proxy still serves it. Put a readable or smaller copy without metadata at the same path in the `media` volume, then run the command again. Photos that are already clean are skipped. Or take the photo off the report. The admin shows photos read-only, so this goes through the shell:

  ```
  docker compose run --rm app python manage.py shell -c "from cirs.models import CriticalIncident; CriticalIncident.objects.filter(photo='photos/2018/01/09/scan.png').update(photo='')"
  docker compose run --rm --user root app rm /media/photos/2018/01/09/scan.png
  ```

- **`cleaned <old> -> <new> (old file not removed: <error>)`** The clean copy is in place and the database points to it, but the old file with its metadata and its old name is still on disk, usually because of a wrong owner. It counts like a failure in the exit status. Remove it by hand with the `rm` command above, using the old name from the line.

The command is safe to run again at any time.

### 8. Start

```
docker compose up -d --build
```

The app container runs `migrate` on every start. Now it finds nothing to do.

### 9. Check

Compare with the numbers from before the dump. Run on the new system:

```
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select (select count(*) from cirs_department) as departments, (select count(*) from cirs_criticalincident) as incidents, (select count(*) from cirs_publishableincident where publish) as published, (select count(*) from cirs_comment) as comments, (select count(*) from auth_user) as users;"'
```

Then check by hand:

- Log in as a QM user with the old password. Open the incident list in the admin and a few cases that you know, including their dates, texts, translations and comments.
- Open a published case with a photo, in the public list and in the admin.
- Enter an old 8-character code under "My report" and read the case.
- Report a test incident, note the code, open it with the code, answer as QM and read the answer as the reporter.
- Ask for a password reset mail, if you configured mail.

If a count differs, do not go live. Restore the dump again into a fresh database (`docker compose down -v`, then start from step 1) and look at the first step that goes wrong.

### 10. Accept and clean up

Keep the old dump and the media archive until the QM has accepted the new system. Then delete them. The dump holds every report in clear text. Remove the old installation after that, not before.

To go back before you have accepted: `docker compose down`, start the old system again. Reports made on the new system in the meantime are not in the old one.

## Rehearse first

**Whole path with synthetic data.** `scripts/test-upgrade-from-v5.sh` needs Docker, git (with the tag `v5.2.1`) and openssl, and takes about two minutes once the images are pulled. It uses the project name `labcirs-upgrade` and removes everything it created. It prints `UPGRADE OK` and exits 0 when all of these hold after the upgrade: the counts of departments, incidents, published cases, comments and users; text, dates, categories and code of every incident; the translations of the published case; the permissions of the reviewer; no registration tables, content types or permissions; `migrate --check` clean; the photo renamed and free of metadata; the old password hash still valid; and the old code, the public list and the admin pages working.

**Your schema without your reports.** To see whether the migrations run on your database, restore only its structure and its migration history into a scratch database. No report leaves the old server:

```
pg_dump -Fc --schema-only -f schema.dump labcirs
pg_dump -Fc --data-only -t 'django_migrations*' -t 'django_content_type*' -t 'auth_permission*' -f state.dump labcirs
```

On the scratch host, with only `db` running, restore both files (`--data-only` for the second one), then run steps 4 and 5. The patterns with `*` take the id counters (sequences) of these tables along; `pg_restore -l state.dump` lists a `SEQUENCE SET` entry for each. The tables are empty, so the steps for photos and checks do not apply. This tests the migrations on your real schema. The full path above tests the commands with data.
