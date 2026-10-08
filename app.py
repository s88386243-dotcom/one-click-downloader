import os
import re
import sys
import time
import uuid
import shutil
import socket
import ipaddress
import threading
from urllib.parse import urlparse
from datetime import datetime

# Ensure utf-8 encoding on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from flask import Flask, render_template, request, jsonify, send_file, abort

# Configure FFmpeg: prioritize system ffmpeg (Linux/Docker) then fallback to imageio_ffmpeg
FFMPEG_PATH = shutil.which("ffmpeg")
if not FFMPEG_PATH:
    try:
        import imageio_ffmpeg
        raw_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        bin_dir = os.path.dirname(raw_ffmpeg)
        ffmpeg_alias = os.path.join(bin_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if not os.path.exists(ffmpeg_alias):
            try:
                shutil.copy2(raw_ffmpeg, ffmpeg_alias)
            except Exception:
                pass
        FFMPEG_PATH = ffmpeg_alias if os.path.exists(ffmpeg_alias) else raw_ffmpeg
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    except Exception as e:
        print(f"[Warning] Could not initialize imageio-ffmpeg: {e}")

import yt_dlp

app = Flask(__name__)

# Base directory for downloads
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# In-memory download tasks tracking
tasks = {}

def sanitize_filename(name):
    """Sanitize string for safe filenames across platforms."""
    name = re.sub(r'[\\/*?:"<>|]', "", name or "")
    name = re.sub(r'[\s_]+', " ", name).strip()
    return name[:80] if name else "video"

def format_duration(seconds):
    """Convert seconds into HH:MM:SS or MM:SS."""
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

def is_safe_url(url_string):
    """
    Validate URL to protect against SSRF (Server-Side Request Forgery)
    and internal network probing.
    """
    try:
        parsed = urlparse(url_string)
        if parsed.scheme not in ("http", "https"):
            return False, "Invalid URL scheme. Sirf http aur https allowed hain."

        hostname = parsed.hostname
        if not hostname:
            return False, "Invalid hostname in URL."

        hostname_lower = hostname.lower()

        # Block localhost and local loopback domains
        if hostname_lower in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "local"):
            return False, "Localhost addresses are not allowed."

        # Resolve hostname to check for private / internal IP ranges
        try:
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_reserved
                or ip_obj.is_multicast
            ):
                return False, "Private or internal network URLs are blocked for security."
        except socket.gaierror:
            return False, "Host address could not be resolved."

        return True, None
    except Exception as e:
        return False, f"URL validation error: {str(e)}"

def detect_platform(url):
    """Detect source platform from URL."""
    url_l = (url or "").lower()
    if "instagram.com" in url_l:
        return "instagram"
    elif "facebook.com" in url_l or "fb.watch" in url_l:
        return "facebook"
    elif "youtube.com" in url_l or "youtu.be" in url_l:
        return "youtube"
    elif "tiktok.com" in url_l:
        return "tiktok"
    elif "twitter.com" in url_l or "x.com" in url_l:
        return "twitter"
    return "other"

def cleanup_old_files():
    """Periodically remove files older than 15 minutes from downloads folder."""
    while True:
        try:
            time.sleep(300)  # every 5 minutes
            now = time.time()
            if os.path.exists(DOWNLOADS_DIR):
                for f in os.listdir(DOWNLOADS_DIR):
                    f_path = os.path.join(DOWNLOADS_DIR, f)
                    if os.path.isfile(f_path):
                        if now - os.path.getmtime(f_path) > 900:  # 15 minutes
                            try:
                                os.remove(f_path)
                            except Exception:
                                pass
        except Exception:
            pass

# Start cleanup thread in daemon mode
cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/info", methods=["POST"])
def get_video_info():
    """Fetch video metadata securely without downloading."""
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"success": False, "error": "Kripya video URL enter karein (Please provide a URL)"}), 400

    if len(url) > 2048:
        return jsonify({"success": False, "error": "URL bahut lamba hai (URL length exceeded limit)"}), 400

    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    # SSRF Protection
    is_safe, err_reason = is_safe_url(url)
    if not is_safe:
        return jsonify({"success": False, "error": err_reason or "Invalid URL entered."}), 400

    platform = detect_platform(url)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 15,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                return jsonify({"success": False, "error": "Video details fetch nahi ho paye. Kripya URL check karein."}), 400

            title = info.get("title") or "Video"
            uploader = info.get("uploader") or info.get("channel") or info.get("creator") or platform.capitalize()
            duration_secs = info.get("duration")
            duration_str = format_duration(duration_secs)
            thumbnail = info.get("thumbnail") or ""
            
            # Parse available video qualities
            formats = info.get("formats") or []
            heights = set()
            for f in formats:
                h = f.get("height")
                if h and isinstance(h, int):
                    heights.add(h)

            quality_options = []
            
            # 1080p Full HD
            if any(h >= 1080 for h in heights) or platform in ["instagram", "facebook"]:
                quality_options.append({"id": "1080", "label": "Full HD (1080p)", "ext": "mp4", "badge": "1080p MP4", "desc": "Best Quality"})
            
            # 720p HD
            if any(h >= 720 for h in heights) or platform in ["instagram", "facebook"]:
                quality_options.append({"id": "720", "label": "HD (720p)", "ext": "mp4", "badge": "720p MP4", "desc": "Standard HD"})

            # 480p / 360p SD
            quality_options.append({"id": "480", "label": "SD (480p)", "ext": "mp4", "badge": "480p MP4", "desc": "Fast & Light"})
            
            # Always offer Best Available as default first option
            quality_options.insert(0, {"id": "best", "label": "Maximum Quality (Best)", "ext": "mp4", "badge": "Best MP4", "desc": "Auto Highest Res"})

            # MP3 Audio option
            quality_options.append({"id": "mp3", "label": "Audio Only (MP3)", "ext": "mp3", "badge": "MP3 Audio", "desc": "High Quality 192k"})

            return jsonify({
                "success": True,
                "data": {
                    "url": url,
                    "title": title,
                    "uploader": uploader,
                    "duration": duration_str,
                    "thumbnail": thumbnail,
                    "platform": platform,
                    "qualities": quality_options
                }
            })
    except Exception as e:
        err_msg = str(e)
        if "Private video" in err_msg or "login" in err_msg.lower():
            err_text = "Yeh video private hai ya isme login ki zaroorat hai."
        elif "Unsupported URL" in err_msg:
            err_text = "Unsupported URL. Kripya valid YouTube, Instagram ya Facebook link dalein."
        else:
            err_text = "Video fetch karne me error aaya. Kripya link verify karein."
        return jsonify({"success": False, "error": err_text}), 400

def run_download_task(task_id, url, quality, title_hint):
    """Execute download in background thread securely and update progress."""
    tasks[task_id] = {
        "status": "downloading",
        "percent": 0,
        "speed": "Connecting...",
        "eta": "--",
        "filename": "",
        "filepath": "",
        "error": None
    }

    sanitized_title = sanitize_filename(title_hint)
    output_template = os.path.join(DOWNLOADS_DIR, f"{task_id}_{sanitized_title}.%(ext)s")

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            percent = round((downloaded / total * 100), 1) if total > 0 else 0
            
            speed_bytes = d.get("speed")
            if speed_bytes:
                if speed_bytes > 1024 * 1024:
                    speed_str = f"{speed_bytes / (1024 * 1024):.1f} MB/s"
                else:
                    speed_str = f"{speed_bytes / 1024:.0f} KB/s"
            else:
                speed_str = "Downloading..."

            eta_seconds = d.get("eta")
            eta_str = f"{eta_seconds}s" if eta_seconds is not None else "--"

            tasks[task_id]["percent"] = percent
            tasks[task_id]["speed"] = speed_str
            tasks[task_id]["eta"] = eta_str
            tasks[task_id]["status"] = "downloading"

        elif d.get("status") == "finished":
            tasks[task_id]["percent"] = 100
            tasks[task_id]["status"] = "processing"
            tasks[task_id]["speed"] = "Processing / Merging..."

    ydl_opts = {
        "outtmpl": output_template,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    }

    if FFMPEG_PATH:
        ydl_opts["ffmpeg_location"] = FFMPEG_PATH

    if quality == "mp3":
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    elif quality == "1080":
        ydl_opts["format"] = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        ydl_opts["merge_output_format"] = "mp4"
    elif quality == "720":
        ydl_opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        ydl_opts["merge_output_format"] = "mp4"
    elif quality == "480":
        ydl_opts["format"] = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]/best"
        ydl_opts["merge_output_format"] = "mp4"
    else:  # best
        ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
        ydl_opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Locate generated file
        target_file = None
        for f in os.listdir(DOWNLOADS_DIR):
            if f.startswith(task_id):
                target_file = os.path.join(DOWNLOADS_DIR, f)
                break

        if target_file and os.path.exists(target_file):
            final_ext = os.path.splitext(target_file)[1]
            display_name = f"{sanitized_title}{final_ext}"
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["percent"] = 100
            tasks[task_id]["filepath"] = target_file
            tasks[task_id]["filename"] = display_name
            tasks[task_id]["speed"] = "Done!"
        else:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = "Downloaded file could not be located."
    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["error"] = "Download failed. Kripya dobara try karein."

@app.route("/api/download/start", methods=["POST"])
def start_download():
    """Start asynchronous download task with strict validation."""
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    quality = (data.get("quality") or "best").strip().lower()
    title = (data.get("title") or "video").strip()

    if not url:
        return jsonify({"success": False, "error": "URL missing"}), 400

    if len(url) > 2048:
        return jsonify({"success": False, "error": "URL length exceeded limit"}), 400

    # Whitelist quality parameter to prevent parameter pollution
    allowed_qualities = {"best", "1080", "720", "480", "360", "mp3"}
    if quality not in allowed_qualities:
        quality = "best"

    # SSRF Protection
    is_safe, err_reason = is_safe_url(url)
    if not is_safe:
        return jsonify({"success": False, "error": err_reason or "Invalid URL entered."}), 400

    task_id = uuid.uuid4().hex[:12]

    thread = threading.Thread(
        target=run_download_task,
        args=(task_id, url, quality, title),
        daemon=True
    )
    thread.start()

    return jsonify({"success": True, "task_id": task_id})

@app.route("/api/download/status/<task_id>", methods=["GET"])
def check_status(task_id):
    """Check progress of a download task with sanitized task_id."""
    if not re.match(r"^[a-fA-F0-9]{8,32}$", task_id):
        return jsonify({"status": "error", "error": "Invalid task ID format"}), 400

    task = tasks.get(task_id)
    if not task:
        return jsonify({"status": "not_found", "error": "Task not found"}), 404

    return jsonify({
        "status": task["status"],
        "percent": task.get("percent", 0),
        "speed": task.get("speed", ""),
        "eta": task.get("eta", ""),
        "filename": task.get("filename", ""),
        "error": task.get("error")
    })

@app.route("/api/download/file/<task_id>", methods=["GET"])
def download_file(task_id):
    """Send completed file to client with strict path-traversal prevention."""
    if not re.match(r"^[a-fA-F0-9]{8,32}$", task_id):
        abort(400, description="Invalid task ID format")

    task = tasks.get(task_id)
    if not task or task.get("status") != "completed":
        abort(404, description="File not ready or expired")

    filepath = task.get("filepath")
    if not filepath or not os.path.exists(filepath):
        abort(404, description="File not found on server")

    # Path traversal validation: ensure target file is strictly inside DOWNLOADS_DIR
    real_downloads_dir = os.path.realpath(DOWNLOADS_DIR)
    real_filepath = os.path.realpath(filepath)
    if not real_filepath.startswith(real_downloads_dir + os.sep):
        abort(403, description="Access forbidden: Path outside downloads directory")

    filename = task.get("filename") or os.path.basename(filepath)
    return send_file(
        filepath,
        as_attachment=True,
        download_name=filename,
        mimetype="audio/mpeg" if filename.endswith(".mp3") else "video/mp4"
    )

@app.after_request
def add_security_headers(response):
    """
    Comprehensive Security Hardening:
    - Protects against Clickjacking (X-Frame-Options: SAMEORIGIN)
    - Prevents MIME-type sniffing (X-Content-Type-Options: nosniff)
    - Enforces modern HTTPS (Strict-Transport-Security)
    - Mitigates XSS & Code Injection (Content-Security-Policy)
    - Restricts Referrer leaking (Referrer-Policy)
    - Disallows risky browser APIs (Permissions-Policy)
    - Restricts CORS: No open wildcard '*' so other sites cannot read user responses
    """
    # 1. Prevent MIME-type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"

    # 2. Prevent Clickjacking
    response.headers["X-Frame-Options"] = "SAMEORIGIN"

    # 3. Enforce Strict HTTPS
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

    # 4. Referrer Policy
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # 5. Restrict permissions
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

    # 6. Content Security Policy (allows self, FontAwesome CDN, Google Fonts, and Monetag ad domains)
    csp_policy = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com https://*.monetag.com https://*.alwingulla.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https:; "
        "frame-src 'self' https:; "
        "object-src 'none'; "
        "base-uri 'self';"
    )
    response.headers["Content-Security-Policy"] = csp_policy

    # Note: Removed 'Access-Control-Allow-Origin: *' to fix Antideploy Medium vulnerability.
    # Same-origin requests work natively without CORS, blocking malicious external sites.

    return response

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print(f"[+] One Click Downloader starting on port {port}...")
    print(f"[+] Open in browser: http://127.0.0.1:{port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)
