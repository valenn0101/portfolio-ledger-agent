# Getting started

[Back to the project](../README.md)

## Run with Docker (recommended)

Requirements: Docker Engine or Docker Desktop with Compose. Run the commands below from the repository root.

```bash
cp .env.example .env
```

Add your OpenAI Platform key to `.env`:

```dotenv
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5.6-terra
```

Then start the application:

```bash
docker compose up --build -d
```

Open <http://127.0.0.1:8765>. To stop it:

```bash
docker compose down
```

`compose.yaml` bind-mounts `./data` into the container. Rebuilding or replacing the container therefore does not delete the SQLite database or generated workbook.

The server reads the `PORT` environment variable when a hosting provider assigns one. Local Docker Compose continues to use port `8765` by default.

On Linux/WSL, Compose defaults to UID/GID `1000:1000` so SQLite can write to the bind mount without running the container as root. If your account uses different values, set `LOCAL_UID` and `LOCAL_GID` in `.env` using the output of `id -u` and `id -g`.

## Run without Docker

Python 3.12 or newer is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./iniciar_con_ia.sh
```

The application loads `.env` automatically and opens <http://127.0.0.1:8765>.

## Private access

Authentication remains optional for local development. Enable it for every deployment reachable from the internet:

```dotenv
APP_REQUIRE_AUTH=1
APP_USERNAME=your_username
APP_PASSWORD=a-long-unique-password
APP_SESSION_SECRET=a-random-secret-with-at-least-32-characters
APP_SESSION_HOURS=12
APP_SECURE_COOKIES=auto
```

Generate a session secret without reusing the login password:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

The server refuses to start when authentication is enabled with missing or weak values. Passwords and session secrets are read only from the environment. The browser receives a signed, expiring, HTTP-only cookie; API credentials and the login password are never stored in browser storage. Repeated failed logins are temporarily throttled. `/api/health` remains public so a hosting platform can monitor the service, while the interface, Excel download, and all portfolio APIs require a valid session.

## Deploy on Railway

The repository includes `railway.toml` and a Docker health check. A straightforward private single-user deployment is:

1. Create a Railway project from the GitHub repository and let it detect the `Dockerfile`.
2. Add a persistent volume mounted at `/app/data`; this keeps SQLite and the generated workbook across redeploys.
3. Add the OpenAI and market-data variables plus all `APP_*` authentication variables shown above. Use `APP_SECURE_COOKIES=always` for the Railway HTTPS domain.
4. Set `RAILWAY_RUN_UID=0`. Railway volumes are mounted as root, so this runtime override lets the existing image write to `/app/data`.
5. Generate a public domain only after the health check is green and authentication has been tested.

Railway supplies `PORT` automatically. Do not add the local `data/` directory or `.env` to the service image. See Railway's official [volume guide](https://docs.railway.com/volumes) and [Dockerfile deployment guide](https://docs.railway.com/guides/dockerfiles) for the current platform steps and limits.
