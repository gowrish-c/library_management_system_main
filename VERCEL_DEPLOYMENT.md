# Vercel + Neon deployment

This version uses **Neon Serverless Postgres** instead of SQLite. Neon has a native Vercel integration and is designed for serverless workloads. The app uses SQLAlchemy + psycopg with `NullPool` and disables server-side prepared statements to avoid PgBouncer/transaction-pooler statement reuse errors.

## 1. Create the database

In Vercel, open **Marketplace / Integrations → Neon** and connect a Neon Postgres database to this project. Vercel can provision/connect the Neon resource and inject database environment variables. You can also create a Neon project directly and copy its pooled PostgreSQL connection string.

## 2. Environment variables

Set these in Vercel for Production (and Preview if desired):

- `DATABASE_URL` = Neon PostgreSQL connection string
- `SECRET_KEY` = a long random secret

Do not commit either secret to GitHub.

If the password contains special URL characters such as `@`, use the URL-encoded value supplied by Neon rather than manually rebuilding the connection string.

## 3. No SQL/table creation is required

The Flask application calls `db.create_all()` automatically after the SQLAlchemy models are loaded. It also seeds demo users/books when the database is empty.

## 4. Demo accounts

- Admin: `admin` / `admin123`
- Librarian: `librarian1` / `librarian123`
- Student: `student1` / `student123`

Change demo passwords before real use.

## 5. Deploy

Push the project to GitHub and import the repository into Vercel. The included `vercel.json` points Vercel to `api/index.py`.

After deployment, `/health` returns database status. Open it if you want to verify the database connection.

## 6. Live behavior

The UI sends AJAX requests and refreshes affected sections immediately after an action. It also polls live data every 1.5 seconds while the page is visible, so a request/approval/issue/return made on another device appears without manually reloading the page.
