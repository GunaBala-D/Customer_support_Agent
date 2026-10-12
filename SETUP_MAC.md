# SupportPilot: setup on a Mac

Runs fully on your Mac (Apple Silicon or Intel). No paid API needed. Takes about 15 minutes, mostly model downloads.

## 1. Install the prerequisites (once)

Open **Terminal** and run:

```bash
xcode-select --install                 # gives you git and make (skip if already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # Homebrew, skip if installed
brew install python@3.12 node@22 ollama
```

Check the versions: `python3.12 --version` (3.11 or newer is required) and `node --version` (18 or newer).

## 2. Unzip and install

```bash
cd ~/Downloads && unzip SupportPilot.zip && cd SupportPilot
make install PYTHON=python3.12
```

This creates a Python virtual environment in `backend/.venv`, installs the backend and frontend dependencies, and copies `.env.example` to `.env`.

## 3. Start Ollama and download the models

```bash
ollama serve                           # leave this running (or open the Ollama app instead)
```

In a **second Terminal tab**:

```bash
cd ~/Downloads/SupportPilot
make models                            # downloads llama3.2:3b (~2 GB) and nomic-embed-text (~270 MB)
```

On a Mac with 8 GB RAM `llama3.2:3b` is a good fit. With 16 GB or more you can try `llama3.1:8b`: put `OLLAMA_MODEL=llama3.1:8b` in `.env`
and run `ollama pull llama3.1:8b`. You can also change the model later from the app's **Settings** page.

## 4. Run the app

```bash
make dev                               # backend on :8000 and frontend on :5173
```

Open **http://localhost:5173**. On first start the app creates its database, loads the sample customers/orders/documents and indexes the
documents (about 30–60 seconds the first time; watch the Terminal). Then sign in with one of the demo accounts shown on the login screen:

| Account | E-mail | Password | What it can do |
|---|---|---|---|
| Admin | `admin@supportpilot.example` | `admin-demo-1` | everything, including ⚙ Settings |
| Agent | `agent@supportpilot.example` | `demo-password` | ticket inbox |
| Customer | `aisha.khan@example.com` | `demo-password` | chat with order tools |

Try: "What is your refund policy?", "Where is order ORD-1024?" (as the customer), "Please cancel order ORD-1001" (it will ask you to confirm).
API documentation: http://localhost:8000/docs. Stop everything with `Ctrl+C`.

## 5. Other commands

```bash
make test        # run the automated tests
make evaluate    # measure answer quality on your model (writes evaluation/reports/latest.md)
make lint        # code style and type checks
make seed        # reload demo data and re-index documents
```

## Optional: PostgreSQL instead of SQLite

```bash
brew install postgresql@16 && brew services start postgresql@16
createuser -P support                  # choose a password when asked
createdb -O support support
```

Either put `DATABASE_URL=postgresql+psycopg://support:YOUR_PASSWORD@127.0.0.1:5432/support` in `.env`, or switch while the app is running:
**⚙ Settings → Database → Change database**.

## Optional: Docker

Install Docker Desktop, keep `ollama serve` running on the Mac (it uses your GPU), then:

```bash
cp .env.example .env
docker compose up --build              # UI http://localhost:3000, API http://localhost:8000/docs
```

This has not been tested on the machine it was built on. See the notes in `README.md`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `make: command not found` | `xcode-select --install` |
| `python3.12: command not found` | `brew install python@3.12`, then open a new Terminal tab |
| Login screen can't reach the server | the backend isn't running: check the Terminal running `make dev` for errors |
| Answers say "couldn't find reliable information" for everything | the documents aren't indexed: make sure Ollama is running and `make models` finished, then **Settings → Rebuild index** |
| Status badge in the sidebar is red | Ollama isn't running or the model isn't pulled: `ollama serve` and `make models` |
| First answer takes a minute | the model is loading into memory; later answers take a few seconds |
| `Address already in use` (port 8000/5173) | `lsof -ti :8000 \| xargs kill` (change the port number for 5173) |
| `ollama` pull is slow or stalls | re-run `make models`; it resumes where it stopped |
| Want a clean start | stop the app, delete `data/app.db`, `data/chroma` and `data/runtime_settings.json`, then `make dev` |

For production use read the **Security** section of `README.md` first (set `APP_ENV=production`, `JWT_SECRET` and `ADMIN_PASSWORD`).
