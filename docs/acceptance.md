# Acceptance run against the function list

Every function of the old LabCIRS, per role, was checked in a real browser against the modernised version, with the production image behind nginx. This page lists each point with its result and where the evidence is. All data in the test are invented. The evidence files are in `artifacts/`, which git ignores; the commands below create them again.

**Result: 162 browser checks, 162 passed. Visual check: 15 pages at 3 widths, no finding. Six defects were found and fixed (see "Found and fixed"). Two small points stay open (see "Open findings"). The checks ran before defects 3 to 6 were fixed; the unit tests (579 OK) and the browser tests in `functional_tests` (95 OK) ran after.**

## How the acceptance runs

```
sh scripts/acceptance.sh reset    # a fresh stack: db, app (gunicorn), proxy (nginx, TLS), mailpit
sh scripts/acceptance.sh run      # visual check, function list, checks inside the containers
sh scripts/acceptance.sh down     # remove everything again
```

- The stack is the production image and the production settings (`DEBUG` off, `ALLOWED_HOSTS` set, gunicorn, whitenoise with a manifest, the nginx of `deploy/nginx.conf.template`). `scripts/compose.accept.yaml` takes back what `compose.dev.yaml` does to the app (test image, `runserver`, `DEBUG` on) and everything starts through `scripts/dc`, in the Compose project `labcirs-accept`. It never touches another project.
- `seed_demo_data` fills it: one department, the reviewer `qm-demo`, the superuser `admin-demo`, six reports in every status (one with the old code `ab#d$f-9`), two published cases and 30 more for paging, replies of both sides, organisational units A and B with two sub-units each. The passwords of the two accounts are made by `acceptance.sh`, kept in `artifacts/accept/accounts.env` and never printed.
- The browser is Chromium in `mcr.microsoft.com/playwright/python:v1.63.0-noble`, mails go to `axllent/mailpit:v1.31.3`, both pinned in `compose.dev.yaml` (profile `e2e`). The site is `https://cirs.test` inside the stack (self-signed certificate).
- `LABCIRS_DEFAULT_FROM_EMAIL` is set in the stack, so the report form offers the e-mail field. The nginx rate limits are raised to 600 a minute, because the script sends many forms in a minute; `scripts/smoke-test.sh` tests the limits (see F5).
- `scripts/abnahme.py` drives the browser and reads the database where the page cannot show a fact (counts, a deleted address, the hash of a new password). Each check prints `PASS` or `FAIL` with the values it compared. The checks depend on each other (the report of A3 is published in R4), so the run needs a fresh stack.

Evidence, relative to the repository root:

| What | Where |
|---|---|
| All 162 checks with the compared values | `artifacts/accept/abnahme-result.txt` (also `.json`) |
| Screenshots of the flows (1440 px) | `artifacts/accept/screens/<point>-<step>.png` |
| Visual check, 45 screenshots and the table | `artifacts/sicht/`, `artifacts/accept/sichtkontrolle.txt` |
| Proof that the visual check can fail | `artifacts/accept/sichtkontrolle-selftest.txt` |
| Mails of Mailpit as text | `artifacts/accept/mails/` |
| Photo before and after, language switch | `artifacts/accept/files/` |
| Settings, logs, language switch (inside the containers) | `artifacts/accept/container-checks.txt`, `artifacts/accept/logs/` |
| Unit tests, migration check, smoke test | `artifacts/accept/unit-suite.txt`, `artifacts/accept/r7-check.txt`, `artifacts/accept/smoke-test.txt` |
| The two defects before the fix | `artifacts/accept/before-fix/` |

To see the lines of one point: `grep -E '^(PASS|FAIL) +A3 ' artifacts/accept/abnahme-result.txt`.

## Visual check

`sh scripts/acceptance.sh dc run --rm -T playwright python scripts/sichtkontrolle.py` opens every public page and the admin index at 320, 768 and 1440 px. For each page and width it saves a screenshot, measures horizontal scrolling (`scrollWidth > clientWidth`), lists console messages that name the Content Security Policy (plus the violation events of the page) and lists every request to another origin.

| Pages (15) | Start, case list, search, page 2, report form, form with errors, success page with code, "My report", wrong code, report with code, login, failed login, password reset, 404 page, admin index (QM) |
|---|---|
| Horizontal scrolling | none at 320, 768, 1440 px |
| CSP messages | 0 |
| Requests to other origins | 0 |
| Result | `NO FINDINGS`, exit status 0 |

`--selftest` puts a style attribute, a request to another origin and a table wider than the page into a page by hand and exits with 0 only if the check finds all three (`SELFTEST OK`). The same CSP and origin watch runs through all flows of `abnahme.py` (check in section SEC: 0 messages, 0 foreign requests, in the public pages and the admin).

## Function list

Results are the number of checks that passed out of the checks that ran. Screenshot names are in `artifacts/accept/screens/`.

### Anonymous

| Point | Function | Result | Evidence |
|---|---|---|---|
| A1 | The start page leads to the case list | 4/4 | `A1-start-page.png`. With two active departments the start page lists them (`A1-department-list.png`), with one it goes to the list again. |
| A2 | Published cases with title, description, measures, photo, month/year; search; paging | 9/9 | `A2-case-with-photo.png`, `A2-search.png`, `A2-search-empty.png`, `A2-paging-page-1.png`, `A2-paging-page-2.png`. 32 cases give 25 on page 1 and 7 on page 2. |
| A3 | Report with all fields incl. organisational unit and photo, then the success page with the code | 6/6 | `A3-form-filled.png`, `A3-success-code.png`, `A3-success-reloaded.png` (the code is shown once). The database holds every field. |
| A4 | With the code: own report, status, replies; answer | 8/8 | `A4-code-entry.png`, `A4-detail-seeded.png` (roles instead of names), `A4-reply-sent.png`, `A4-qm-reply-seen-by-reporter.png`. "End access" closes the report. |
| A5 | Language switch only with several languages | 2/2 and the container check | `A5-language-switch-de.png`, `A5-language-switch-en.png`. `files/A5-switch-two-languages.txt` (2 languages, 2 buttons) and `files/A5-switch-one-language.txt` (1 language, 0 buttons): the same page in a one-off container with one language. |
| A6 | Report with e-mail | 19/19 | `A6-form-email-field.png`, `A6-success-with-email.png`, `A6-admin-no-address.png`, `A6-my-report-email-card.png`, `A6-address-deleted.png`, `A6-mailpit-inbox.png`, `mails/A6-reporter-mails.txt`. Confirmation with code, a mail for a new status, for a reply of the QM and the closing mail; none carries report content. After "completed" the address is gone from the database and no more mail comes. The QM sees the address on no admin page (9 pages tried) and gets 403 when it tries to delete it. The reporter deletes it under "My report" only with the confirmation box. |

### Quality management (reviewer)

| Point | Function | Result | Evidence |
|---|---|---|---|
| R1 | The login leads to the admin | 4/4 | `R1-login-form.png`, `R1-login-failed.png`, `R1-admin-index.png`. The technical reporter account cannot log in. |
| R2 | List with all filters, incl. organisational unit | 14/14 | `R2-list-all.png`, `R2-filter-*.png`. Status, both dates, consent, risk, organisational unit and "has publishable incident" each show the count the database gives, two filters work together. The department filter shows as soon as the QM has two departments (`R2-filter-department.png`; Django hides a filter with one choice). |
| R3 | The report part is read-only and shows the photo; the review block is complete | 8/8 | `R3-incident-change-review-open.png`, `R3-incident-saved.png`. No input exists for the report part, a forged field is ignored, the thumbnail loads, all nine review fields are there and save. |
| R4 | Publish by inline and by list, with the mandatory languages | 12/12 | `R4-inline-mandatory-languages-error.png`, `R4-inline-english-tab.png`, `R4-inline-published.png`, `R4-inline-no-consent.png`, `R4-list-mandatory-languages-error.png`, `R4-list-published.png`, `R4-public-list-new-case.png`. German only is refused, German and English publish, a report without consent cannot be published, the public list shows the result. |
| R5 | Edit `LabCIRSConfig` | 6/6 | `R5-config-validation-error.png`, `R5-config-saved.png`, `R5-login-info-on-login-page.png`. See "Found and fixed", point 1. |
| R6 | Change name and password of the reporter account | 6/6 | `R6-user-list.png`, `R6-name-changed.png`, `R6-password-changed.png`, `R6-other-user-refused.png`. The QM sees only the reporter account of its department. |
| R7 | See replies in the admin, answer in the frontend | 8/8 | `R7-comments-in-admin.png`, `R7-qm-reply-frontend.png`, `R7-comments-in-admin-after-reply.png`. The reply stands under "Qualitätsmanagement" and is saved under the QM account. |
| R8 | Maintain organisational units | 6/6 | `R8-orgunit-list.png`, `R8-orgunit-add-child.png`, `R8-orgunit-list-after.png`. A new unit appears in the report form, an inactive one does not, the QM cannot delete. |
| R9 | Notification mail for a new report and a new reply, without content | 5/5 | `mails/R9-notification-mails.txt`, `R9-mailpit-inbox.png`. The body is the text of the configuration, subject and body hold no report content. |

### Superuser

| Point | Function | Result | Evidence |
|---|---|---|---|
| S1 | Admin for users, departments, roles and configuration; no reports visible | 10/10 | `S1-admin-index.png`, `S1-no-reports.png`, `S1-user-list.png`, `S1-role-added.png`, `S1-department-add.png`, `S1-departments.png`, `S1-configs.png`. The report list is empty while the database holds 42 reports. A new user, a reporter role and a second department were made through the admin. |
| S2 | Password reset by mail | 8/8 | `S2-reset-form.png`, `S2-reset-sent.png`, `S2-reset-new-password.png`, `S2-reset-done.png`, `S2-reset-link-used.png`, `mails/S2-password-reset-mail.txt`. The link is `https://cirs.test/accounts/reset/…`, it works once, the old password stops working. |

### Security

| Check | Result | Evidence |
|---|---|---|
| No IP address in the logs of `proxy` and `app` | pass: 906 requests logged, no address | `artifacts/accept/container-checks.txt`, `artifacts/accept/logs/proxy.log`, `app.log`. The bind and loopback addresses are removed first, IPv4 only (as in `scripts/smoke-test.sh`). |
| No user agent in the logs | pass | same files |
| A photo from a phone has no EXIF data afterwards | pass | `files/photo-before.json` (6 EXIF entries, 4 GPS entries, device and author), `files/photo-after.json` (none, upright 400x600, random file name). Read with Pillow from the file the proxy delivers. |
| `DEBUG` off, `ALLOWED_HOSTS` set | pass | `container-checks.txt` (settings of the running container); a request with another `Host` gets 400; an error page shows no traceback and no settings. |
| Security headers, secure cookies | pass | CSP without `unsafe-inline`, HSTS, `nosniff`, CSRF cookie `Secure`. |
| Targeted checks F1 to F5 | pass | next table |

### Targeted checks

| Point | What was done | Result |
|---|---|---|
| F1 | A person without a code changes the pk in the detail URL (GET) and sends a reply to a report of someone else (POST), with and without the code of another report | 302 to the code entry in every case, no data in the answer, the number of replies in the database is unchanged (6 before and after). 404 for a pk that does not exist or a wrong department. 4/4 |
| F2 | The old code `ab#d$f-9` typed as ` AB#D$F-9 `, `ab #d$f -9`, `Ab#D$f-9` | Recognised every time and leads to the report; a wrong old code is refused. `F2-legacy-code-detail.png`. 6/6 |
| F3 | TIFF, HEIC, a JPEG of 10.6 MB, a cut-off JPEG, a text file named `.jpg` | A form error in German each time, HTTP 200, no report saved (40 before and after). `F3-photo-error-tiff.png`, `F3-photo-error-too-big.png`. A turned JPEG is stored upright and without metadata (see Security). 6/6 |
| F4 | `/incidents/gibtsnicht/create/` and an inactive department (switched off in the admin) | 404 for GET and POST (with a valid CSRF token), also for the list and the code entry; no report is made. `F4-not-found.png`. 3/3 plus the inactive department in S1 |
| F5 | POST forms through nginx and HTTPS | Every form of the flows (code check, report, reply, login, password reset) passed through `https://cirs.test` without a 403. `scripts/smoke-test.sh` passes too (`artifacts/accept/smoke-test.txt`: code check passes CSRF, 45 fast POSTs: 27 answered 429, no IP address in the logs) with `LABCIRS_CSRF_TRUSTED_ORIGINS` and a non-standard port. |

## Found and fixed

Each has a test.

1. **Translations were cached per worker** (found in R5). After the QM saved a new login info, the login page showed the old text on about every second request: gunicorn starts three workers, and parler keeps translations in the cache of its own process (the default `LocMemCache`). The same held for a published case that the QM edited. Fix: `PARLER_ENABLE_CACHING = False`. Before: `artifacts/accept/before-fix/R5-stale-cache-curl.txt` (12 requests, two different texts) and `R5-login-info-stale-in-one-worker.png`. After: R5 passes with the same check.
2. **The incident page in the admin scrolled sideways at 1440 px** (found in R3 and R4). The three fields of the "publishable incident" inline (size 62 and two textareas of 60 columns) set the table width and stuck out beside the navigation. Fix: in an inline cell the fields take the width of the cell. Before: `artifacts/accept/before-fix/R3-admin-inline-overflow-1440.png` (page 1879 px wide). After: the flows measure `scrollWidth` against `clientWidth` on seven admin pages, all 1440 against 1440.
3. **The title of the comment block on the incident page was the English "Comments".** `CommentInline` now sets a translatable `verbose_name`. No migration.
4. **The incident page showed the field "Photo" twice**, first the stored path, then the thumbnail. Only the thumbnail stays.
5. **"Review" (title of the review block) and "Cirs" (application name in the breadcrumbs) were not translated.** The German title is "Bewertung", the application name is "CIRS" in both languages.
6. **The admin index at 320 px broke "Organisationseinheiten" inside the word.** The names in the admin tables now use `hyphens: auto`. Firefox hyphenates the word at 320 px; a browser without a German hyphenation dictionary still cuts it.

## Open findings

None of these blocks use of the system.

1. In the review block of the incident page, the category labels break inside words at 1440 px ("Organisation/Kommunikatio n").
2. Some strings come from Django's own German catalog and are not translated there: the line "In case you've forgotten, you are: …" in the password reset mail, the hint "Enable password-based authentication…" on the user form, "Please correct the errors below.".
3. What this run could not show: a real mail server, a real TLS certificate, a real phone camera (the file is made to look like one), other browsers than Chromium (the Firefox tests in `functional_tests` cover that), and the Debian host with host networking. The stack ran on Docker Desktop with a self-signed certificate. Repeat the smoke test on the host before you rely on a stack.
