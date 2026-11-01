# Installation

## Windows

Prerequisites: Python 3.11 or later, PostgreSQL, Node.js, and npm.

```powershell
cd Server
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Create an empty PostgreSQL database and edit `Server/.env`:

```dotenv
DATABASE_URL=postgresql+psycopg://<database-user>:<database-password>@localhost:5432/<database>
SECRET_KEY=<long-random-secret>
```

Apply migrations:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Temporarily set `ADMIN_EMAIL`, `ADMIN_PASSWORD`, and optionally `ADMIN_FULL_NAME` in `.env`, then seed the first administrator:

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_admin
```

Remove `ADMIN_EMAIL` and `ADMIN_PASSWORD` from `.env` after seeding. Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

In a second terminal:

```powershell
cd Client
npm.cmd install
npm.cmd run dev
```

Open `http://localhost:5173`. API documentation is at `http://localhost:8000/docs`.

## Linux and macOS

```sh
cd Server
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m scripts.seed_admin
.venv/bin/python -m uvicorn app.main:app --reload
```

In another terminal, run `npm install` and `npm run dev` from `Client`.
