# Visitor Verification Portal

## Files
- `app.py` — Flask application
- `requirements.txt` — Python dependencies

## Run locally on Windows (Command Prompt)

1. Open Command Prompt in this folder.
2. Install packages:

   `py -m pip install -r requirements.txt`

3. Set local test settings in that same Command Prompt window:

   `set SECRET_KEY=replace-with-a-long-random-secret`

   `set ADMIN_PASSWORD=choose-a-strong-test-password`

   `set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/REPLACE_WITH_A_NEW_WEBHOOK`

   `set COOKIE_SECURE=0`

   `set GEO_LOOKUP=0`

4. Start:

   `py app.py`

5. Open `http://127.0.0.1:5000`
6. Admin page: `http://127.0.0.1:5000/admin/login`

## Hosting
Use `gunicorn app:app` as the start command on a Linux host.
Set these environment variables in the host's dashboard:
- `SECRET_KEY`: long random secret
- `ADMIN_PASSWORD`: strong unique admin password
- `DISCORD_WEBHOOK_URL`: newly generated Discord webhook URL (keep private)
- `COOKIE_SECURE=1` when HTTPS is enabled
- `GEO_LOOKUP=0` by default; set to `1` only if you want approximate IP-based lookup
- `DATABASE_PATH`: optional path on persistent storage

## Privacy and safety
The page tells visitors what is collected. It does not request GPS or precise location.
When `GEO_LOOKUP=1`, the visitor IP is sent to ipapi.co for approximate location.
On hosted platforms, `request.remote_addr` may be a proxy address rather than the visitor's actual IP.
Never publish your webhook URL or put it in source code. If it has been exposed, regenerate it.
