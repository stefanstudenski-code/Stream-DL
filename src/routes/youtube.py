import os
import json
import uuid
import tempfile
import shutil
from flask import Blueprint, request, Response, stream_with_context, send_file, after_this_request
from threading import Thread
from queue import Queue
import yt_dlp
import re
import time

ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')

# Create a Blueprint for the YouTube routes
youtube_bp = Blueprint('youtube', __name__)

# ====== NEUE FUNKTION: COOKIES HANDELN ======
def setup_cookies():
    """Erstellt eine temporäre Cookie-Datei aus der Umgebungsvariable YT_COOKIES"""
    cookies = os.environ.get('YT_COOKIES')
    if cookies:
        # Erstelle temporäre Datei
        cookie_file = tempfile.NamedTemporaryFile(delete=False, suffix='.txt')
        cookie_path = cookie_file.name
        cookie_file.write(cookies.strip().encode('utf-8'))
        cookie_file.close()
        print("✅ Cookies from environment variable written to temporary file")
        return cookie_path
    return None

def cleanup_cookies(cookie_path):
    """Löscht die temporäre Cookie-Datei"""
    if cookie_path and os.path.exists(cookie_path):
        try:
            os.unlink(cookie_path)
            print("🧹 Temporary cookie file deleted")
        except Exception as e:
            print(f"⚠️ Error deleting cookie file: {e}")

def parse_duration(seconds):
    """Konvertiert Sekunden in einen formatierten String (HH:MM:SS)."""
    if seconds is None:
        return "N/A"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

# -------------------------------
# Neuer Endpoint: Datei direkt zurückgeben
# -------------------------------
@youtube_bp.route('/analyze')
def analyze_url():
    """Analysiert die URL, um Videoinformationen und verfügbare Formate zu extrahieren."""
    video_url = request.args.get('url')
    if not video_url:
        return Response(json.dumps({'error': 'URL parameter is required'}), status=400, mimetype='application/json')

    cookie_path = None
    try:
        # ====== COOKIES EINBINDEN ======
        cookie_path = setup_cookies()
        
        ydl_opts = {'quiet': True}
        if cookie_path:
            ydl_opts['cookiefile'] = cookie_path
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            video_formats = []
            audio_formats = []
            
            # Regulärer Ausdruck, um die Qualität (z.B. 1080p) aus dem format_note zu extrahieren
            quality_re = re.compile(r'(\d{3,4}p)')

            for f in info.get('formats', []):
                # Ignoriere Formate ohne Audio und Video (z.B. nur Storyboards)
                if f.get('acodec') == 'none' and f.get('vcodec') == 'none':
                    continue

                filesize = f.get('filesize') or f.get('filesize_approx')
                filesize_str = f"{filesize / (1024*1024):.2f} MB" if filesize else "N/A"

                # Für Videoformate (mit Video-Codec)
                if f.get('vcodec') != 'none':
                    # Extrahiere Qualität aus format_note, sonst Fallback
                    quality_match = quality_re.search(f.get('format_note', ''))
                    quality = quality_match.group(1) if quality_match else f.get('resolution', 'N/A')
                    
                    video_formats.append({
                        'format_id': f['format_id'],
                        'quality': quality,
                        'ext': f.get('ext'),
                        'filesize': filesize_str,
                        'has_audio': f.get('acodec') != 'none'
                    })
                
                # Für reine Audioformate (kein Video-Codec)
                elif f.get('vcodec') == 'none' and f.get('acodec') != 'none':
                    audio_formats.append({
                        'format_id': f['format_id'],
                        'quality': f"{f.get('abr')}k" if f.get('abr') else "Beste",
                        'ext': f.get('ext'),
                        'filesize': filesize_str
                    })

            # Sortiere Formate
            video_formats.sort(key=lambda x: int(x['quality'].replace('p', '')) if x['quality'][:-1].isdigit() else 0, reverse=True)
            audio_formats.sort(key=lambda x: int(x['quality'].replace('k', '')) if x['quality'][:-1].isdigit() else 0, reverse=True)

            response_data = {
                'title': info.get('title'),
                'duration': parse_duration(info.get('duration')),
                'thumbnail': info.get('thumbnail'),
                'video_formats': video_formats,
                'audio_formats': audio_formats
            }
            return Response(json.dumps(response_data), status=200, mimetype='application/json')

    except Exception as e:
        return Response(json.dumps({'error': str(e)}), status=500, mimetype='application/json')
    finally:
        # ====== COOKIES AUFRÄUMEN ======
        cleanup_cookies(cookie_path)

@youtube_bp.route('/download')
def download_video():
    """Handles the video download request via SSE with selectable options."""
    video_url = request.args.get('url')
    download_type = request.args.get('type') # 'audio' or 'video'
    quality = request.args.get('quality') # format_id
    output_format = request.args.get('format') # e.g., 'mp3', 'mp4'
    filename = request.args.get('filename', str(uuid.uuid4()))

    if not all([video_url, download_type, quality, output_format]):
        return Response(json.dumps({'error': 'Missing required parameters'}), status=400, mimetype='application/json')

    # ====== TEMPORÄRER ORDNER FÜR DOWLOADS ======
    temp_dir = tempfile.mkdtemp()
    print(f"📁 Created temporary directory: {temp_dir}")
    
    def cleanup_temp_dir():
        """Löscht den temporären Ordner nach dem Download"""
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                print(f"🧹 Deleted temporary directory: {temp_dir}")
        except Exception as e:
            print(f"⚠️ Error deleting temporary directory: {e}")

    @after_this_request
    def remove_temp_dir(response):
        """Stellt sicher, dass der temporäre Ordner nach der Antwort gelöscht wird"""
        cleanup_temp_dir()
        return response

    def generate_stream():
        q = Queue()
        downloaded_file = None
        cookie_path = None
        
        try:
            # ====== COOKIES EINBINDEN ======
            cookie_path = setup_cookies()

            def download_thread(options):
                nonlocal downloaded_file
                try:
                    options['outtmpl'] = os.path.join(temp_dir, f'{filename}.%(ext)s')
                    
                    with yt_dlp.YoutubeDL(options) as ydl:
                        ydl.download([video_url])
                        # Finde die heruntergeladene Datei
                        for f in os.listdir(temp_dir):
                            if f.startswith(filename):
                                downloaded_file = os.path.join(temp_dir, f)
                                break
                except Exception as e:
                    q.put({'status': 'error', 'message': str(e)})
                finally:
                    q.put({'status': 'done'}) # Signal completion

            def progress_hook(d):
                q.put(d)

            # Build yt-dlp options dynamically
            ydl_opts = {
                'progress_hooks': [progress_hook],
                'quiet': True,
                'noplaylist': True,
            }
            
            # ====== COOKIES EINBINDEN ======
            if cookie_path:
                ydl_opts['cookiefile'] = cookie_path

            if download_type == 'audio':
                ydl_opts['format'] = quality # Select best audio format
                ydl_opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': output_format, # mp3, aac, etc.
                    'preferredquality': '192', # Standard quality
                }]
            elif download_type == 'video':
                ydl_opts['format'] = quality
                ydl_opts['postprocessors'] = [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': output_format, # mp4, mkv, etc.
                }] if output_format != 'mp4' else [] # Only convert if necessary
            
            # Start download in background
            thread = Thread(target=download_thread, args=(ydl_opts,))
            thread.start()

            # Stream progress updates from queue
            while True:
                data_dict = q.get()

                if data_dict['status'] == 'done':
                    break
                
                if data_dict['status'] == 'error':
                    yield f"data: {json.dumps(data_dict)}\n\n"
                    break

                progress = {}
                if data_dict['status'] == 'downloading':
                    percent_str = data_dict.get('_percent_str', '0%').strip().replace('%', '')
                    progress = {
                        'percent': float(ansi_escape.sub('', percent_str).strip()),
                        'status': 'downloading',
                        'message': f"Downloading: {data_dict.get('_percent_str', '')} of {data_dict.get('_total_bytes_str', '')} at {data_dict.get('_speed_str', '')} ETA {data_dict.get('_eta_str', '')}"
                    }
                elif data_dict['status'] == 'finished':
                    progress = {
                        'percent': 100.0,
                        'status': 'finished',
                        'message': "Download abgeschlossen, Konvertierung läuft..."
                    }

                if progress:
                    yield f"data: {json.dumps(progress)}\n\n"
            
            # ====== DATEI IST FERTIG GELADEN ======
            if downloaded_file:
                # Erstelle einen Download-Link für die Datei
                file_url = f"/download_file/{os.path.basename(downloaded_file)}"
                final_message = {
                    'status': 'complete', 
                    'message': 'Prozess abgeschlossen.',
                    'file_url': file_url
                }
                yield f"data: {json.dumps(final_message)}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'error', 'message': 'Download completed but file not found'})}\n\n"
                
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
        finally:
            # ====== COOKIES AUFRÄUMEN ======
            cleanup_cookies(cookie_path)

    return Response(stream_with_context(generate_stream()), mimetype='text/event-stream')

# ====== NEUER ENDPOINT: DATEI HERUNTERLADEN ======
@youtube_bp.route('/download_file/<filename>')
def serve_downloaded_file(filename):
    """Liefert die heruntergeladene Datei und löscht sie danach"""
    # Finde die Datei im temporären Ordner
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, filename)
    
    if not os.path.exists(file_path):
        return Response(json.dumps({'error': 'File not found'}), status=404, mimetype='application/json')
    
    # Lösche die Datei nach dem Senden
    @after_this_request
    def remove_file(response):
        try:
            os.remove(file_path)
            print(f"🧹 Deleted file after download: {file_path}")
            # Leere den temporären Ordner, falls leer
            if not os.listdir(temp_dir):
                os.rmdir(temp_dir)
        except Exception as error:
            print(f"⚠️ Error removing file: {error}")
        return response
    
    return send_file(file_path, as_attachment=True, download_name=filename)
