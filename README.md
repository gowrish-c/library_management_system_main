# Library Management System — Neon + Vercel

Flask Library Management System using persistent PostgreSQL on Neon and deployable from GitHub to Vercel.

## Features

- Admin, librarian and student roles
- Student requests a book
- Librarian/admin approves or rejects a request
- Approval automatically creates the issue record
- Student can cancel pending requests
- Add/edit/delete books
- Issue and return books
- Persistent PostgreSQL database
- Automatic table creation — no manual SQL setup required
- AJAX actions without full-page reload
- Immediate refresh after actions
- Live polling every 1.5 seconds for changes from other users/devices
- Responsive interactive CSS
- Vercel health endpoint at `/health`

## Database

Production uses Neon PostgreSQL. Do not use SQLite on Vercel because the Vercel function filesystem is not durable.

## Deploy

Read `VERCEL_DEPLOYMENT.md`.

## Local

```bash
pip install -r requirements.txt
python app.py
```

Without `DATABASE_URL`, local development uses `library.db`. On Vercel, `DATABASE_URL` is required.
