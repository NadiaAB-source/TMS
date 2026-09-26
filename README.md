# IQARUS Training Management System — latest source

This package contains the complete Django application after the accepted September 2026 layout/correction work and the 26 September clean-start/history-import update.

## Included

- Full Django application in `app/`
- HTML templates, CSS, JavaScript and logo assets
- Course job cards, QR registration, student records, course directory and reports
- Staff access controls, J35 planner and inventory workflows
- All migrations and automated tests
- Historical annotated-data import and date-ordered course numbering
- Blank stamped-list workbook and fictional historical-import example

## Private data

This public-source package does not contain a database, staff accounts, password hashes, secret key, emails, uploaded documents, course lists, stamped lists or runtime logs. The working clean website and its assigned users remain in `IQARUS_TMS_CLEAN` on Google Drive.

## Run or deploy

GitHub stores the source but does not run this Django website. IT must install `app/requirements.txt`, provide private environment settings and a database, run migrations, collect static files and serve the WSGI application. To preserve the currently assigned users, IT must transfer the latest private database separately.

Do not run the old clean-start installer against an existing website.
