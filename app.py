"""
CI/CD Manager and Service Hub for Homelab

Manages:
- Host System Metrics & Docker Containers
- Docker Compose Services in /services
- Light Bot (Power outage monitor)
- Po2Bot (Residential verification bot)
- Meet Streamer (Google Meet bot)
"""

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import os
import subprocess
import yaml
import json
import urllib.request
import urllib.error
import base64
import time
import threading
from datetime import datetime, timezone, timedelta
from functools import wraps
import psutil

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret-key-in-production')

# Paths & Environment
HOST_PROC = os.environ.get('HOST_PROC', '/host/proc' if os.path.exists('/host/proc') else '/proc')
HOST_SYS = os.environ.get('HOST_SYS', '/host/sys' if os.path.exists('/host/sys') else '/sys')
HOST_ROOT = os.environ.get('HOST_ROOT', '/host/root' if os.path.exists('/host/root') else '/')

if os.path.exists(HOST_PROC):
    psutil.PROCFS_PATH = HOST_PROC

COMPOSE_FILE = os.environ.get('COMPOSE_FILE', '/services/docker-compose.yml')
SERVICES_DIR = os.environ.get('SERVICES_DIR', '/services')
USERS_FILE = os.environ.get('USERS_FILE', '/app/users.json')
DOCKER_COMPOSE_CMD = os.environ.get('DOCKER_COMPOSE_CMD', 'docker compose')

# Microservice Endpoints
LIGHT_BOT_URL = os.environ.get('LIGHT_BOT_URL', 'http://light-bot:5000')
PO2BOT_URL = os.environ.get('PO2BOT_URL', 'http://po2bot:8088')
MEET_STREAMER_URL = os.environ.get('MEET_STREAMER_URL', 'http://meet-streamer:8090')
TEMPLATES_FILE = os.environ.get('TEMPLATES_FILE', os.path.join(SERVICES_DIR, 'cicd-manager', 'banner_templates.json'))
AIR_ALERT_STATE_FILE = os.environ.get('AIR_ALERT_STATE_FILE', os.path.join(SERVICES_DIR, 'cicd-manager', 'air_alert_state.json') if os.path.exists(SERVICES_DIR) else '/app/air_alert_state.json')
CURRENT_BANNER_FILE = os.environ.get('CURRENT_BANNER_FILE', os.path.join(SERVICES_DIR, 'cicd-manager', 'current_banner.json') if os.path.exists(SERVICES_DIR) else '/app/current_banner.json')

from air_alert import AirAlertManager

DEFAULT_TEMPLATES = [
    {
        "id": "stem_club",
        "name": "STEM Гурток «Інженерія ШІ»",
        "badge": "ORT STEM CLUB • ONLINE",
        "title": "STEM Гурток «Інженерія ШІ»",
        "schedule": "Понеділок, Середа, П'ятниця • 16:30 – 18:00",
        "subtitle": "Заняття почнеться незабаром",
        "qr": "https://t.me/+T6-bsnhWXZNhZjQy",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "10a1_info",
        "name": "10-А (1 група) • Інформатика",
        "badge": "10-А • ІНФОРМАТИКА (1 ГРУПА)",
        "title": "10-А Клас: Інформатика",
        "schedule": "Код Classroom: n6in3g7l",
        "subtitle": "",
        "qr": "https://t.me/+BcfMtOeORc41MTY6",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "10a2_info",
        "name": "10-А (2 група) • Інформатика",
        "badge": "10-А • ІНФОРМАТИКА (2 ГРУПА)",
        "title": "10-А Клас: Інформатика",
        "schedule": "Код Classroom: kp7i44cu",
        "subtitle": "",
        "qr": "https://t.me/+kbOf90UrwQo0MTIy",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "10b1_info",
        "name": "10-Б (1 група) • Інформатика",
        "badge": "10-Б • ІНФОРМАТИКА (1 ГРУПА)",
        "title": "10-Б Клас: Інформатика",
        "schedule": "Код Classroom: cd2fmnnl",
        "subtitle": "",
        "qr": "https://t.me/+sF5P9a9PElNhNDhi",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "10b2_info",
        "name": "10-Б (2 група) • Інформатика",
        "badge": "10-Б • ІНФОРМАТИКА (2 ГРУПА)",
        "title": "10-Б Клас: Інформатика",
        "schedule": "Код Classroom: xv77hur4",
        "subtitle": "",
        "qr": "https://t.me/+R4A-sch-mwc0ODk6",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "10v1_info",
        "name": "10-В (1 група) • Інформатика",
        "badge": "10-В • ІНФОРМАТИКА (1 ГРУПА)",
        "title": "10-В Клас: Інформатика",
        "schedule": "Код Classroom: f2nxiaeo",
        "subtitle": "",
        "qr": "https://t.me/+bHoRBLLMl8lkMWFi",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "10v2_info",
        "name": "10-В (2 група) • Інформатика",
        "badge": "10-В • ІНФОРМАТИКА (2 ГРУПА)",
        "title": "10-В Клас: Інформатика",
        "schedule": "Код Classroom: vs55sgu4",
        "subtitle": "",
        "qr": "https://t.me/+1Su96ZyyadYwZmIy",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "10g1_info",
        "name": "10-Г (1 група) • Інформатика",
        "badge": "10-Г • ІНФОРМАТИКА (1 ГРУПА)",
        "title": "10-Г Клас: Інформатика",
        "schedule": "Код Classroom: pey2jtvc",
        "subtitle": "",
        "qr": "https://t.me/+TdzXtROFXL0wZjky",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "10g2_info",
        "name": "10-Г (2 група) • Інформатика",
        "badge": "10-Г • ІНФОРМАТИКА (2 ГРУПА)",
        "title": "10-Г Клас: Інформатика",
        "schedule": "Код Classroom: xr53rjd5",
        "subtitle": "",
        "qr": "https://t.me/+xPFR2MMs1tE3M2Uy",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "10a_ai",
        "name": "10-А • Основи інженерії ШІ",
        "badge": "10-А • ОІШІ",
        "title": "Основи інженерії ШІ",
        "schedule": "Код Classroom: tlcn33ge",
        "subtitle": "",
        "qr": "https://t.me/+yp8eR3Pc8e9hYTJi",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "11a_web",
        "name": "11-А • Вебтехнології",
        "badge": "11-А • ВЕБТЕХНОЛОГІЇ",
        "title": "11-А: Вебтехнології",
        "schedule": "Код Classroom: strk6qub",
        "subtitle": "Понеділок • 10:50 – 12:40 (2 пара)",
        "qr": "https://t.me/Fieneek",
        "qr_caption": "Telegram група",
        "bg_image": ""
    },
    {
        "id": "break",
        "name": "Перерва 10 хв",
        "badge": "ПЕРЕРВА • BREAK",
        "title": "Перерва між уроками",
        "schedule": "Урок продовжиться за кілька хвилин",
        "subtitle": "Зробіть чай та розімніться ☕",
        "qr": "",
        "qr_caption": "",
        "bg_image": ""
    },
    {
        "id": "tech_pause",
        "name": "Технічна пауза",
        "badge": "ТЕХНІЧНА ПАУЗА",
        "title": "Налаштування обладнання",
        "schedule": "Трансляція відновиться найближчим часом",
        "subtitle": "Будь ласка, залишайтеся на зв'язку",
        "qr": "",
        "qr_caption": "",
        "bg_image": ""
    }
]


def load_banner_templates():
    paths = [TEMPLATES_FILE, '/app/banner_templates.json']
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        return data
            except Exception as e:
                print(f"Error loading templates from {p}: {e}")
    return DEFAULT_TEMPLATES


def save_banner_templates(templates):
    paths = [TEMPLATES_FILE, '/app/banner_templates.json']
    saved = False
    for p in paths:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(templates, f, ensure_ascii=False, indent=2)
            saved = True
        except Exception as e:
            print(f"Could not save templates to {p}: {e}")
    return saved


def exec_host_command(cmd, timeout=30):
    """Execute command on host system (using nsenter via docker if in container)."""
    if os.path.exists('/var/run/docker.sock') and os.path.exists('/host/root'):
        docker_cmd = (
            f"docker run --rm --privileged --pid=host "
            f"-v /opt/school-meet-streamer:/opt/school-meet-streamer alpine "
            f"nsenter -t 1 -m -u -n -i {cmd}"
        )
        return run_command(docker_cmd, timeout=timeout)
    else:
        return run_command(cmd, timeout=timeout)


def get_shared_tmp():
    """Get shared temporary directory accessible by both container and host."""
    if os.path.exists(SERVICES_DIR):
        c_dir = os.path.join(SERVICES_DIR, 'cicd-manager', 'tmp')
        h_dir = '/root/services/cicd-manager/tmp'
    else:
        c_dir = '/tmp'
        h_dir = '/tmp'
    os.makedirs(c_dir, exist_ok=True)
    return c_dir, h_dir


# Prime CPU percent reading
try:
    psutil.cpu_percent(interval=None)
except Exception:
    pass


def get_light_bot_token():
    """Retrieve Light Bot API token from env or mounted .env file"""
    token = os.environ.get('LIGHT_BOT_TOKEN')
    if token:
        return token
    env_file = os.path.join(SERVICES_DIR, 'light-bot', '.env')
    if os.path.exists(env_file):
        try:
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('API_TOKEN='):
                        return line.split('=', 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return None


def load_users():
    """Load users from JSON file"""
    try:
        with open(USERS_FILE, 'r') as f:
            return json.load(f).get('users', [])
    except Exception as e:
        print(f"Error loading users: {e}")
        return [{
            'username': os.environ.get('ADMIN_USERNAME', 'admin'),
            'password': os.environ.get('ADMIN_PASSWORD', 'admin'),
            'services': ['*']
        }]


def get_user(username):
    """Get user by username"""
    users = load_users()
    for user in users:
        if user['username'] == username:
            return user
    return None


def get_user_services(username):
    """Get list of services user has access to"""
    user = get_user(username)
    if not user:
        return []
    
    allowed_services = user.get('services', [])
    if '*' in allowed_services:
        return list(get_services().keys())
    
    all_services = get_services()
    return [s for s in allowed_services if s in all_services]


def can_access_service(username, service):
    """Check if user can access a specific service"""
    allowed_services = get_user_services(username)
    return service in allowed_services


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def get_services():
    """Parse docker-compose.yml and return list of services with build contexts"""
    try:
        with open(COMPOSE_FILE, 'r') as f:
            compose_data = yaml.safe_load(f)
        
        services = {}
        for service_name, service_config in compose_data.get('services', {}).items():
            service_info = {
                'name': service_name,
                'has_build': 'build' in service_config,
                'build_context': None
            }
            
            if 'build' in service_config:
                if isinstance(service_config['build'], dict):
                    service_info['build_context'] = service_config['build'].get('context', service_name)
                else:
                    service_info['build_context'] = service_config['build']
            
            services[service_name] = service_info
        
        return services
    except Exception as e:
        print(f"Error reading compose file: {e}")
        return {}


def run_command(command, cwd=None, timeout=300):
    """Execute a shell command and return output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd or SERVICES_DIR,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            'success': result.returncode == 0,
            'output': result.stdout + result.stderr,
            'returncode': result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'output': f'Command timed out after {timeout} seconds',
            'returncode': -1
        }
    except Exception as e:
        return {
            'success': False,
            'output': str(e),
            'returncode': -1
        }


def http_request(url, method='GET', headers=None, data=None, timeout=5):
    """Safe internal HTTP requester for microservices."""
    try:
        req_headers = headers or {}
        req_data = None
        if data is not None:
            if isinstance(data, (dict, list)):
                req_data = json.dumps(data).encode('utf-8')
                req_headers['Content-Type'] = 'application/json'
            elif isinstance(data, str):
                req_data = data.encode('utf-8')
        req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode('utf-8', errors='replace')
            try:
                parsed = json.loads(content)
            except Exception:
                parsed = content
            return resp.status, parsed
    except urllib.error.HTTPError as e:
        content = e.read().decode('utf-8', errors='replace')
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = content
        return e.code, parsed
    except Exception as e:
        return 500, {'error': str(e)}


def get_current_banner_config():
    """Get active banner configuration."""
    paths = [CURRENT_BANNER_FILE, '/app/current_banner.json']
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                pass
    templates = load_banner_templates()
    if templates and len(templates) > 0:
        t = templates[0]
        return {
            "title": t.get("title", "STEM Гурток «Інженерія ШІ»"),
            "schedule": t.get("schedule", ""),
            "subtitle": t.get("subtitle", ""),
            "badge": t.get("badge", "ORT STEM CLUB • ONLINE"),
            "qr": t.get("qr", ""),
            "qr_caption": t.get("qr_caption", "Telegram група"),
            "bg_image": t.get("bg_image", "")
        }
    return {}


def save_current_banner_config(cfg):
    """Save active banner configuration to disk."""
    clean_cfg = {
        "title": cfg.get("title", ""),
        "schedule": cfg.get("schedule", ""),
        "subtitle": cfg.get("subtitle", ""),
        "badge": cfg.get("badge", ""),
        "qr": cfg.get("qr", ""),
        "qr_caption": cfg.get("qr_caption", ""),
        "bg_image": cfg.get("bg_image", "")
    }
    paths = [CURRENT_BANNER_FILE, '/app/current_banner.json']
    for p in paths:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(clean_cfg, f, ensure_ascii=False, indent=2)
            break
        except Exception:
            pass


def apply_banner_stream(cfg):
    """Generate Y4M stream on host, update /opt/school-meet-streamer/stream.y4m, and reload camera."""
    container_tmp_dir, host_tmp_dir = get_shared_tmp()
    ts = int(datetime.now(timezone.utc).timestamp())
    config_name = f"poster_cfg_{ts}.json"
    container_cfg_path = f"{container_tmp_dir}/{config_name}"
    host_cfg_path = f"{host_tmp_dir}/{config_name}"
    target_y4m = "/opt/school-meet-streamer/stream.y4m"

    full_cfg = dict(cfg)
    full_cfg["output"] = target_y4m

    try:
        with open(container_cfg_path, 'w', encoding='utf-8') as f:
            json.dump(full_cfg, f, ensure_ascii=False)
    except Exception as e:
        return {'success': False, 'error': f"Failed to write config: {e}"}

    cmd = f"python3 /opt/school-meet-streamer/make_stream.py --config {host_cfg_path}"
    res = exec_host_command(cmd, timeout=30)

    try:
        if os.path.exists(container_cfg_path):
            os.remove(container_cfg_path)
    except Exception:
        pass

    if not res.get('success'):
        return {'success': False, 'error': f"Failed to generate stream: {res.get('output', '')}"}

    # Toggle camera in meeting to reload fake video capture stream
    cam_status, cam_resp = http_request(f"{MEET_STREAMER_URL}/camera/toggle", method='POST', timeout=10)
    return {'success': True, 'camera_reload': cam_resp}


def send_meet_chat_internal(text):
    """Send text message into ongoing Google Meet call."""
    return http_request(
        f'{MEET_STREAMER_URL}/chat',
        method='POST',
        data={'text': text, 'message': text},
        timeout=10
    )


def is_meet_in_call():
    """Check if meet-streamer is currently in a call. Returns True, False, or None if unreachable."""
    status_code, data = http_request(f'{MEET_STREAMER_URL}/status', timeout=6)
    if status_code == 200 and isinstance(data, dict):
        return bool(data.get('inMeeting'))
    return None



def play_meet_alarm(sound_type):
    """Trigger alarm on/off audio broadcast in Google Meet."""
    return http_request(
        f'{MEET_STREAMER_URL}/alarm/play',
        method='POST',
        data={'type': sound_type},
        timeout=30
    )


# Initialize Air Alert Manager
air_alert = AirAlertManager(
    state_file=AIR_ALERT_STATE_FILE,
    get_banner_fn=get_current_banner_config,
    apply_banner_fn=apply_banner_stream,
    send_chat_fn=send_meet_chat_internal,
    is_in_meeting_fn=is_meet_in_call,
    play_alarm_fn=play_meet_alarm,
)
air_alert.start()


@app.route('/')
def index():
    if 'logged_in' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = get_user(username)
        if user and user['password'] == password:
            session['logged_in'] = True
            session['username'] = username
            flash('Successfully logged in!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'error')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Successfully logged out', 'success')
    return redirect(url_for('login'))


@app.route('/dashboard')
@app.route('/dashboard/<tab>')
@login_required
def dashboard(tab='metrics'):
    """Display modern dashboard with tabs and services."""
    username = session.get('username')
    all_services = get_services()
    allowed_service_names = get_user_services(username)
    
    user_services = {name: info for name, info in all_services.items() 
                     if name in allowed_service_names}
    
    return render_template('dashboard.html', 
                           services=user_services, 
                           compose_file=COMPOSE_FILE,
                           username=username,
                           active_tab=tab)


# ==========================================
# SYSTEM METRICS & CONTAINER APIS
# ==========================================

@app.route('/api/system/metrics')
@login_required
def api_system_metrics():
    """Fetch live host resources and docker stats."""
    try:
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count(logical=True)
        
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        
        try:
            du = psutil.disk_usage(HOST_ROOT)
            disk_info = {
                'total': du.total,
                'used': du.used,
                'free': du.free,
                'percent': du.percent
            }
        except Exception:
            disk_info = {'total': 0, 'used': 0, 'free': 0, 'percent': 0}

        # Query Docker stats & status
        stats_res = run_command("docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}'", timeout=10)
        ps_res = run_command("docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.State}}'", timeout=10)
        
        stats_map = {}
        if stats_res['success']:
            for line in stats_res['output'].strip().split('\n'):
                if not line:
                    continue
                parts = line.split('\t')
                if len(parts) >= 4:
                    stats_map[parts[0]] = {
                        'cpu': parts[1],
                        'mem_usage': parts[2],
                        'mem_percent': parts[3]
                    }

        containers = []
        if ps_res['success']:
            for line in ps_res['output'].strip().split('\n'):
                if not line:
                    continue
                parts = line.split('\t')
                if len(parts) >= 4:
                    cname = parts[0]
                    c_stats = stats_map.get(cname, {'cpu': '0.00%', 'mem_usage': '0B / 0B', 'mem_percent': '0.00%'})
                    containers.append({
                        'name': cname,
                        'status': parts[1],
                        'image': parts[2],
                        'state': parts[3],
                        'cpu': c_stats['cpu'],
                        'mem_usage': c_stats['mem_usage'],
                        'mem_percent': c_stats['mem_percent']
                    })

        # Sort running first, then by name
        containers.sort(key=lambda c: (0 if c['state'] == 'running' else 1, c['name']))

        return jsonify({
            'success': True,
            'cpu': {
                'percent': cpu_percent,
                'count': cpu_count
            },
            'ram': {
                'total': vm.total,
                'used': vm.used,
                'free': vm.free,
                'available': vm.available,
                'percent': vm.percent
            },
            'swap': {
                'total': sm.total,
                'used': sm.used,
                'free': sm.free,
                'percent': sm.percent
            },
            'disk': disk_info,
            'containers': containers,
            'timestamp': datetime.now().strftime('%H:%M:%S')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/container/<name>/<action>', methods=['POST'])
@login_required
def api_container_action(name, action):
    """Execute lifecycle action on arbitrary docker container."""
    if action not in ['restart', 'stop', 'start']:
        return jsonify({'success': False, 'output': f'Unsupported action: {action}'}), 400
    
    cmd = f'docker {action} {name}'
    result = run_command(cmd, timeout=60)
    return jsonify(result)


@app.route('/api/container/<name>/logs')
@login_required
def api_container_logs(name):
    """Fetch logs for container."""
    tail = request.args.get('tail', '150')
    cmd = f'docker logs --tail={tail} {name}'
    result = run_command(cmd, timeout=30)
    return jsonify(result)


# ==========================================
# LIGHT BOT APIS
# ==========================================

@app.route('/api/light-bot/status')
@login_required
def api_light_bot_status():
    """Proxy Light Bot locations status."""
    token = get_light_bot_token()
    headers = {'Authorization': f'Bearer {token}'}
    status_code, data = http_request(f'{LIGHT_BOT_URL}/locations', headers=headers, timeout=5)
    
    if status_code != 200 or not isinstance(data, dict):
        return jsonify({'success': False, 'error': f'Light bot unavailable ({status_code})', 'data': data}), 502
    
    locations = data.get('locations', [])
    now = datetime.now(timezone.utc)
    for loc in locations:
        last_str = loc.get('last_updated', '')
        # Parse ISO timestamp: Last updated represents the moment the current status began
        loc['duration_seconds'] = None
        loc['duration_display'] = 'Невідомо'
        loc['since_display'] = 'Невідомо'
        try:
            if 'Last updated: ' in last_str:
                iso_ts = last_str.replace('Last updated: ', '').strip()
                dt = datetime.fromisoformat(iso_ts)
                diff = max(0, int((now - dt).total_seconds()))
                loc['duration_seconds'] = diff
                loc['since_display'] = dt.strftime('%d.%m %H:%M')
                
                days = diff // 86400
                hours = (diff % 86400) // 3600
                minutes = (diff % 3600) // 60
                if days > 0:
                    loc['duration_display'] = f'{days}д {hours}г {minutes}хв'
                elif hours > 0:
                    loc['duration_display'] = f'{hours}г {minutes}хв'
                elif minutes > 0:
                    loc['duration_display'] = f'{minutes} хв'
                else:
                    loc['duration_display'] = '< 1 хв'
        except Exception:
            pass

    return jsonify({
        'success': True,
        'locations': locations,
        'openai_billing': get_openai_billing()
    })


@app.route('/api/light-bot/toggle/<loc_id>', methods=['POST'])
@login_required
def api_light_bot_toggle(loc_id):
    """Toggle or set power status for a location."""
    req_data = request.get_json(silent=True) or {}
    status = req_data.get('status')
    if not status or status not in ['on', 'off']:
        return jsonify({'success': False, 'error': 'Status must be on or off'}), 400
    
    token = get_light_bot_token()
    headers = {'Authorization': f'Bearer {token}'}
    status_code, resp_data = http_request(
        f'{LIGHT_BOT_URL}/power-status/{loc_id}',
        method='POST',
        headers=headers,
        data={'status': status},
        timeout=10
    )
    return jsonify({
        'success': status_code == 200,
        'response': resp_data,
        'status_code': status_code
    })


# ==========================================
# OPENAI BILLING & USAGE APIS
# ==========================================

OPENAI_BILLING_CACHE = {
    'timestamp': 0,
    'data': None
}

def get_ai_services_usage():
    """Inspect homelab services for OpenAI keys and identify shared usage."""
    services_to_check = [
        {
            'id': 'po2bot',
            'name': 'Po2Bot',
            'role': 'Верифікація мешканців та документів ОСББ',
            'tab': 'po2bot',
            'env_paths': [
                os.path.join(SERVICES_DIR, 'po2bot', '.env'),
                '/root/services/po2bot/.env'
            ]
        },
        {
            'id': 'light-bot',
            'name': 'Light Bot',
            'role': 'Моніторинг відключень світла & сповіщення',
            'tab': 'lightbot',
            'env_paths': [
                os.path.join(SERVICES_DIR, 'light-bot', '.env'),
                '/root/services/light-bot/.env'
            ]
        },
        {
            'id': 'idea_factory',
            'name': 'Idea Factory',
            'role': 'Фабрика ідей / AI помічник для учнів',
            'tab': 'school',
            'env_paths': [
                os.path.join(SERVICES_DIR, 'Head_project', 'idea_factory', '.env'),
                os.path.join(SERVICES_DIR, 'idea_factory', '.env'),
                '/root/services/Head_project/idea_factory/.env'
            ]
        }
    ]

    found_services = []
    key_groups = {}

    for s in services_to_check:
        key = None
        model = None
        for p in s['env_paths']:
            if os.path.exists(p):
                try:
                    with open(p, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith('OPENAI_API_KEY='):
                                key = line.split('=', 1)[1].strip('"\'')
                            elif line.startswith('OPENAI_MODEL='):
                                model = line.split('=', 1)[1].strip('"\'')
                except Exception:
                    pass
            if key:
                break

        if key:
            masked = key[:7] + '...' + key[-4:] if len(key) > 12 else '***'
            s_info = {
                'id': s['id'],
                'name': s['name'],
                'role': s['role'],
                'tab': s['tab'],
                'key_masked': masked,
                'key_raw': key,
                'model': model or 'gpt-4o-mini',
                'shared': False,
                'shared_with': []
            }
            found_services.append(s_info)
            if key not in key_groups:
                key_groups[key] = []
            key_groups[key].append(s['name'])

    has_shared_keys = False
    for s_info in found_services:
        shared_list = key_groups.get(s_info['key_raw'], [])
        if len(shared_list) > 1:
            has_shared_keys = True
            s_info['shared'] = True
            s_info['shared_with'] = [name for name in shared_list if name != s_info['name']]
        del s_info['key_raw']

    warning_text = None
    if has_shared_keys:
        all_shared = []
        for grp in key_groups.values():
            if len(grp) > 1:
                all_shared.extend(grp)
        names_str = ', '.join(dict.fromkeys(all_shared))
        warning_text = f"Увага: Сервіси ({names_str}) використовують один спільний API-ключ OpenAI! Їхні запити ділять спільні ліміти (RPM/TPM) та бюджет організації."

    return {
        'services': found_services,
        'has_shared_keys': has_shared_keys,
        'shared_warning': warning_text
    }


OPENAI_BILLING_LOCK = threading.Lock()
OPENAI_CACHE_FILE = os.path.join(SERVICES_DIR, 'cicd-manager', 'tmp', 'openai_billing_cache.json') if os.path.exists(SERVICES_DIR) else '/app/openai_billing_cache.json'
OPENAI_CACHE_TTL = 600  # 10 minutes cache TTL to prevent API rate limits and overuse

OPENAI_BILLING_CACHE = {
    'timestamp': 0,
    'data': None
}

def load_openai_cache_from_disk():
    """Load cached billing data from disk on startup if present."""
    try:
        if os.path.exists(OPENAI_CACHE_FILE):
            with open(OPENAI_CACHE_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if isinstance(saved, dict) and 'timestamp' in saved and 'data' in saved:
                    OPENAI_BILLING_CACHE['timestamp'] = saved['timestamp']
                    OPENAI_BILLING_CACHE['data'] = saved['data']
    except Exception as e:
        print(f"Error loading OpenAI billing cache: {e}")

load_openai_cache_from_disk()

def save_openai_cache_to_disk(timestamp, data):
    """Atomically save cached billing data to disk."""
    try:
        os.makedirs(os.path.dirname(OPENAI_CACHE_FILE), exist_ok=True)
        tmp_file = OPENAI_CACHE_FILE + '.tmp'
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': timestamp, 'data': data}, f)
        os.replace(tmp_file, OPENAI_CACHE_FILE)
    except Exception as e:
        print(f"Error saving OpenAI billing cache: {e}")


def get_openai_billing(force_refresh=False):
    """Retrieve OpenAI API key info, project name, credit balance, or month cost with thread-safe caching."""
    now = time.time()
    if not force_refresh and OPENAI_BILLING_CACHE['data']:
        age = now - OPENAI_BILLING_CACHE['timestamp']
        if age < OPENAI_CACHE_TTL:
            res = dict(OPENAI_BILLING_CACHE['data'])
            res['cached'] = True
            res['cache_age_seconds'] = int(age)
            res['cache_ttl_seconds'] = OPENAI_CACHE_TTL
            return res

    with OPENAI_BILLING_LOCK:
        now = time.time()
        age = now - OPENAI_BILLING_CACHE['timestamp']
        if not force_refresh and OPENAI_BILLING_CACHE['data'] and age < OPENAI_CACHE_TTL:
            res = dict(OPENAI_BILLING_CACHE['data'])
            res['cached'] = True
            res['cache_age_seconds'] = int(age)
            res['cache_ttl_seconds'] = OPENAI_CACHE_TTL
            return res

        admin_key = os.environ.get('OPENAI_ADMIN_KEY')
        api_key = os.environ.get('OPENAI_API_KEY')

    candidate_paths = [
        os.path.join(SERVICES_DIR, 'cicd-manager', '.env'),
        os.path.join(SERVICES_DIR, 'po2bot', '.env'),
        os.path.join(SERVICES_DIR, 'light-bot', '.env'),
        os.path.join(SERVICES_DIR, '.env'),
        '/root/services/cicd-manager/.env',
        '/root/services/po2bot/.env',
        '/root/services/light-bot/.env',
        '/root/services/.env'
    ]

    for p in candidate_paths:
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not admin_key and line.startswith('OPENAI_ADMIN_KEY='):
                            admin_key = line.split('=', 1)[1].strip('"\'')
                        elif not api_key and line.startswith('OPENAI_API_KEY='):
                            api_key = line.split('=', 1)[1].strip('"\'')
            except Exception:
                pass

    effective_key = api_key or admin_key
    if not effective_key:
        res = {'success': False, 'valid': False, 'error': 'API-ключ не налаштовано'}
        OPENAI_BILLING_CACHE['timestamp'] = now
        OPENAI_BILLING_CACHE['data'] = res
        return res

    masked = effective_key[:7] + '...' + effective_key[-4:] if len(effective_key) > 12 else '***'
    billing_data = {
        'success': True,
        'valid': True,
        'key_masked': masked,
        'has_balance': False,
        'balance_usd': None,
        'cost_usd': None,
        'project_cost_usd': None,
        'org_cost_usd': None,
        'spent_7d': None,
        'spent_30d': None,
        'daily_costs': [],
        'project_name': None,
        'project_id': None,
        'key_name': None,
        'needs_admin_key': False,
        'user': None,
        'org': None,
        'info': None,
        'balance_note': 'OpenAI блокує доступ до credit_grants через API-ключі (доступно лише через веб-браузер на platform.openai.com/settings/organization/billing/overview)',
        'billing_overview_url': 'https://platform.openai.com/settings/organization/billing/overview'
    }

    query_key = admin_key or effective_key
    headers = {'Authorization': f'Bearer {query_key}', 'User-Agent': 'cicd-manager'}

    # 1. Query /v1/me to verify user & org
    try:
        req = urllib.request.Request('https://api.openai.com/v1/me', headers=headers)
        with urllib.request.urlopen(req, timeout=4) as r:
            me_data = json.loads(r.read())
            billing_data['user'] = me_data.get('name') or me_data.get('email')
            orgs = me_data.get('orgs', {}).get('data', [])
            if orgs:
                billing_data['org'] = orgs[0].get('title') or orgs[0].get('name')
    except Exception as e:
        billing_data['info'] = str(e)

    # 2. If admin_key is present, match project and query usage/costs
    target_project_id = None
    target_project_name = None
    target_key_name = None

    if admin_key:
        try:
            req = urllib.request.Request('https://api.openai.com/v1/organization/projects', headers=headers)
            with urllib.request.urlopen(req, timeout=4) as r:
                projects_data = json.loads(r.read())

            key_suffix = effective_key[-4:] if len(effective_key) >= 4 else ''
            for p in projects_data.get('data', []):
                pid = p.get('id')
                pname = p.get('name')
                try:
                    k_req = urllib.request.Request(f'https://api.openai.com/v1/organization/projects/{pid}/api_keys', headers=headers)
                    with urllib.request.urlopen(k_req, timeout=4) as kr:
                        keys_data = json.loads(kr.read())
                        for k in keys_data.get('data', []):
                            redacted = k.get('redacted_value', '')
                            if redacted.endswith(key_suffix):
                                target_project_id = pid
                                target_project_name = pname
                                target_key_name = k.get('name')
                                break
                except Exception:
                    pass
                if target_project_id:
                    break
        except Exception:
            pass

        billing_data['project_id'] = target_project_id
        billing_data['project_name'] = target_project_name
        billing_data['key_name'] = target_key_name

        # Query daily costs for last 30 days & calculate 7d / 30d / month spend
        try:
            now_dt = datetime.now(timezone.utc)
            start_30d = int((now_dt - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            start_month_str = now_dt.strftime('%Y-%m-01')

            # Fetch daily buckets (1d) for last 30 days
            url_costs = f'https://api.openai.com/v1/organization/costs?start_time={start_30d}&bucket_width=1d&limit=100'
            if target_project_id:
                url_costs += f'&project_ids={target_project_id}'

            req = urllib.request.Request(url_costs, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as r:
                c_data = json.loads(r.read())
                daily_list = []
                for b in c_data.get('data', []):
                    iso = b.get('end_time_iso') or ''
                    d = iso.split('T')[0] if 'T' in iso else str(iso)
                    val = sum(res.get('amount', {}).get('value', 0.0) for res in b.get('results', []))
                    daily_list.append({'date': d, 'cost': round(val, 6)})

                billing_data['daily_costs'] = daily_list
                billing_data['spent_30d'] = round(sum(d['cost'] for d in daily_list), 4)
                billing_data['spent_7d'] = round(sum(d['cost'] for d in daily_list[-7:]), 4)

                month_costs = [d['cost'] for d in daily_list if d['date'] >= start_month_str]
                cost_m = round(sum(month_costs), 4)
                billing_data['cost_usd'] = cost_m
                billing_data['project_cost_usd'] = cost_m
                billing_data['has_balance'] = True

            # Query total org cost for month if project filter was applied
            if target_project_id:
                start_month_ts = int(now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
                org_req = urllib.request.Request(f'https://api.openai.com/v1/organization/costs?start_time={start_month_ts}', headers=headers)
                with urllib.request.urlopen(org_req, timeout=4) as r:
                    org_data = json.loads(r.read())
                    tot_org = sum(
                        sum(res.get('amount', {}).get('value', 0.0) for res in b.get('results', []))
                        for b in org_data.get('data', [])
                    )
                    billing_data['org_cost_usd'] = round(tot_org, 4)
            else:
                billing_data['org_cost_usd'] = billing_data['cost_usd']
        except Exception as e:
            billing_data['info'] = str(e)
    else:
        billing_data['needs_admin_key'] = True

    ai_usage = get_ai_services_usage()
    billing_data['ai_services'] = ai_usage
    billing_data['has_shared_keys'] = ai_usage['has_shared_keys']
    billing_data['shared_warning'] = ai_usage['shared_warning']

    OPENAI_BILLING_CACHE['timestamp'] = now
    OPENAI_BILLING_CACHE['data'] = billing_data
    save_openai_cache_to_disk(now, billing_data)

    res = dict(billing_data)
    res['cached'] = False
    res['cache_age_seconds'] = 0
    res['cache_ttl_seconds'] = OPENAI_CACHE_TTL
    res['cached_at'] = datetime.fromtimestamp(now, timezone.utc).isoformat()
    return res


@app.route('/api/openai/billing')
@app.route('/api/ai/billing')
@login_required
def api_openai_billing():
    """Get OpenAI billing and usage information."""
    force = request.args.get('force', '').lower() in ['1', 'true', 'yes']
    return jsonify(get_openai_billing(force_refresh=force))


# ==========================================
# PO2BOT APIS
# ==========================================

@app.route('/api/po2bot/status')
@login_required
def api_po2bot_status():
    """Proxy Po2Bot status, pending count, and OpenAI billing info."""
    status_code, data = http_request(f'{PO2BOT_URL}/status', timeout=4)
    billing = get_openai_billing()
    if status_code != 200:
        return jsonify({'success': False, 'status': 'offline', 'error': str(data), 'openai_billing': billing}), 200
    
    if isinstance(data, dict):
        data['success'] = True
        data['openai_billing'] = billing
        return jsonify(data)
    return jsonify({'success': False, 'status': 'offline', 'openai_billing': billing})



@app.route('/api/po2bot/pending')
@login_required
def api_po2bot_pending():
    """Proxy Po2Bot pending registration requests."""
    status_code, data = http_request(f'{PO2BOT_URL}/pending', timeout=4)
    if status_code != 200:
        return jsonify({'success': False, 'pending': [], 'error': str(data)}), 200
    if isinstance(data, dict):
        data['success'] = True
        return jsonify(data)
    return jsonify({'success': True, 'pending': []})


# ==========================================
# MEET STREAMER APIS
# ==========================================

@app.route('/api/meet/status')
@login_required
def api_meet_status():
    """Proxy Meet Streamer live call status."""
    status_code, data = http_request(f'{MEET_STREAMER_URL}/status', timeout=4)
    if status_code != 200:
        return jsonify({'success': False, 'inMeeting': False, 'error': str(data)}), 200
    if isinstance(data, dict):
        data['success'] = True
        return jsonify(data)
    return jsonify({'success': False, 'inMeeting': False})


@app.route('/api/meet/chat', methods=['POST'])
@login_required
def api_meet_chat():
    """Send text message into ongoing Google Meet call."""
    req_data = request.get_json(silent=True) or {}
    message = req_data.get('message', '').strip()
    if not message:
        return jsonify({'success': False, 'error': 'Message cannot be empty'}), 400
    
    status_code, resp = http_request(
        f'{MEET_STREAMER_URL}/chat',
        method='POST',
        data={'text': message, 'message': message},
        timeout=10
    )
    is_ok = status_code == 200 and isinstance(resp, dict) and (resp.get('ok') is True or resp.get('status') == 'ok')
    return jsonify({
        'success': is_ok,
        'response': resp
    })


@app.route('/api/meet/camera', methods=['POST'])
@login_required
def api_meet_camera_toggle():
    """Toggle camera in meeting."""
    status_code, resp = http_request(f'{MEET_STREAMER_URL}/camera/toggle', method='POST', timeout=10)
    return jsonify({
        'success': status_code == 200 and isinstance(resp, dict) and resp.get('ok', True),
        'response': resp
    })


@app.route('/api/meet/mic', methods=['POST'])
@login_required
def api_meet_mic_toggle():
    """Toggle microphone in meeting."""
    status_code, resp = http_request(f'{MEET_STREAMER_URL}/mic/toggle', method='POST', timeout=10)
    return jsonify({
        'success': status_code == 200 and isinstance(resp, dict) and resp.get('ok', True),
        'response': resp
    })


@app.route('/api/meet/pin', methods=['POST'])
@login_required
def api_meet_pin():
    """Pin bot video for everyone in meeting."""
    status_code, resp = http_request(f'{MEET_STREAMER_URL}/pin', method='POST', timeout=10)
    return jsonify({
        'success': status_code == 200 and isinstance(resp, dict) and resp.get('ok', True),
        'response': resp
    })


@app.route('/api/meet/join', methods=['POST'])
@login_required
def api_meet_join():
    """Start meet-streamer container and join Google Meet."""
    try:
        status_code, data = http_request(f'{MEET_STREAMER_URL}/status', timeout=3)
        if status_code == 200 and isinstance(data, dict) and data.get('inMeeting'):
            return jsonify({'success': True, 'message': 'Бот уже у дзвінку', 'inMeeting': True})

        if status_code == 200:
            cmd = "docker compose -f /opt/school-meet-streamer/docker-compose.yml restart"
        else:
            cmd = "docker compose -f /opt/school-meet-streamer/docker-compose.yml up -d"

        res = exec_host_command(cmd, timeout=30)
        return jsonify({
            'success': res.get('success', False),
            'output': res.get('output', ''),
            'message': 'Запущено підключення до Google Meet'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/meet/leave', methods=['POST'])
@login_required
def api_meet_leave():
    """Leave current meeting and stop container to avoid auto-rejoin and free resources."""
    try:
        try:
            http_request(f'{MEET_STREAMER_URL}/leave', method='POST', timeout=4)
        except Exception:
            pass

        cmd = "docker compose -f /opt/school-meet-streamer/docker-compose.yml stop"
        res = exec_host_command(cmd, timeout=20)
        return jsonify({
            'success': True,
            'output': res.get('output', 'Stopped'),
            'message': 'Бот залишив зустріч, контейнер зупинено'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/meet/templates', methods=['GET'])
@login_required
def api_meet_templates_get():
    """Get saved banner templates."""
    return jsonify({
        'success': True,
        'templates': load_banner_templates()
    })


@app.route('/api/meet/templates', methods=['POST'])
@login_required
def api_meet_templates_save():
    """Save or update banner template."""
    req_data = request.get_json(silent=True) or {}
    template = req_data.get('template')
    templates = req_data.get('templates')

    current_templates = load_banner_templates()

    if templates and isinstance(templates, list):
        current_templates = templates
    elif template and isinstance(template, dict):
        tpl_id = template.get('id') or f"custom_{int(datetime.now(timezone.utc).timestamp())}"
        template['id'] = tpl_id
        found = False
        for idx, t in enumerate(current_templates):
            if t.get('id') == tpl_id:
                current_templates[idx] = template
                found = True
                break
        if not found:
            current_templates.append(template)

    save_banner_templates(current_templates)
    return jsonify({
        'success': True,
        'templates': current_templates
    })


@app.route('/api/meet/templates/<template_id>', methods=['DELETE'])
@login_required
def api_meet_templates_delete(template_id):
    """Delete a template by ID."""
    current_templates = load_banner_templates()
    filtered = [t for t in current_templates if t.get('id') != template_id]
    if len(filtered) == 0:
        filtered = DEFAULT_TEMPLATES
    save_banner_templates(filtered)
    return jsonify({
        'success': True,
        'templates': filtered
    })


@app.route('/api/meet/poster/preview', methods=['POST'])
@login_required
def api_meet_poster_preview():
    """Generate instant PNG preview of banner and return base64 data URI."""
    data = request.get_json(silent=True) or {}
    title = data.get('title', 'STEM Гурток «Інженерія ШІ»')
    schedule = data.get('schedule', '')
    subtitle = data.get('subtitle', '')
    badge = data.get('badge', 'ORT STEM CLUB • ONLINE')
    qr = data.get('qr', '')
    qr_caption = data.get('qr_caption', 'Telegram група')
    bg_image = data.get('bg_image', '')
    image_data = data.get('image_data', '')

    container_tmp_dir, host_tmp_dir = get_shared_tmp()
    ts = int(datetime.now(timezone.utc).timestamp())
    preview_file_name = f"meet_preview_{ts}.png"
    host_preview_png = f"{host_tmp_dir}/{preview_file_name}"
    container_preview_png = f"{container_tmp_dir}/{preview_file_name}"

    host_bg_path = bg_image
    if image_data and ',' in image_data:
        try:
            header, b64data = image_data.split(',', 1)
            raw_bytes = base64.b64decode(b64data)
            bg_name = f"custom_bg_{ts}.png"
            container_bg_path = f"{container_tmp_dir}/{bg_name}"
            with open(container_bg_path, 'wb') as f:
                f.write(raw_bytes)
            host_bg_path = f"{host_tmp_dir}/{bg_name}"
        except Exception as e:
            print(f"Failed to decode image_data: {e}")

    config_name = f"poster_cfg_{ts}.json"
    container_cfg_path = f"{container_tmp_dir}/{config_name}"
    host_cfg_path = f"{host_tmp_dir}/{config_name}"

    cfg = {
        "title": title,
        "schedule": schedule,
        "subtitle": subtitle,
        "badge": badge,
        "qr": qr,
        "qr_caption": qr_caption,
        "bg_image": host_bg_path,
        "preview": host_preview_png
    }

    try:
        with open(container_cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False)
    except Exception as e:
        return jsonify({'success': False, 'error': f"Failed to write config: {e}"}), 500

    cmd = f"python3 /opt/school-meet-streamer/make_stream.py --config {host_cfg_path}"
    res = exec_host_command(cmd, timeout=15)

    try:
        if os.path.exists(container_cfg_path):
            os.remove(container_cfg_path)
    except Exception:
        pass

    if not res['success'] or not os.path.exists(container_preview_png):
        return jsonify({
            'success': False,
            'error': f"Failed to generate preview: {res['output']}"
        }), 500

    try:
        with open(container_preview_png, 'rb') as f:
            png_bytes = f.read()
        b64_img = base64.b64encode(png_bytes).decode('ascii')
        try:
            os.remove(container_preview_png)
        except Exception:
            pass
        return jsonify({
            'success': True,
            'image': f"data:image/png;base64,{b64_img}"
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/meet/poster/apply', methods=['POST'])
@login_required
def api_meet_poster_apply():
    """Generate Y4M stream on host, update /opt/school-meet-streamer/stream.y4m, and reload camera."""
    data = request.get_json(silent=True) or {}
    title = data.get('title', 'STEM Гурток «Інженерія ШІ»')
    schedule = data.get('schedule', '')
    subtitle = data.get('subtitle', '')
    badge = data.get('badge', 'ORT STEM CLUB • ONLINE')
    qr = data.get('qr', '')
    qr_caption = data.get('qr_caption', 'Telegram група')
    bg_image = data.get('bg_image', '')
    image_data = data.get('image_data', '')

    container_tmp_dir, host_tmp_dir = get_shared_tmp()

    host_bg_path = bg_image
    if image_data and ',' in image_data:
        try:
            header, b64data = image_data.split(',', 1)
            raw_bytes = base64.b64decode(b64data)
            bg_name = "meet_custom_bg.png"
            container_bg_path = f"{container_tmp_dir}/{bg_name}"
            with open(container_bg_path, 'wb') as f:
                f.write(raw_bytes)
            host_bg_path = f"{host_tmp_dir}/{bg_name}"
        except Exception as e:
            print(f"Failed to decode image_data: {e}")

    cfg = {
        "title": title,
        "schedule": schedule,
        "subtitle": subtitle,
        "badge": badge,
        "qr": qr,
        "qr_caption": qr_caption,
        "bg_image": host_bg_path,
    }

    save_current_banner_config(cfg)

    # Check if air alert is active to overlay alert attributes
    alert_status = air_alert.get_status()
    if alert_status.get('is_alert') or alert_status.get('alert_level') in ['red', 'yellow', 'green']:
        cfg['alert_level'] = alert_status.get('alert_level')
        cfg['alert_time'] = alert_status.get('alert_start_time') if alert_status.get('is_alert') else alert_status.get('alert_end_time')
        cfg['alert_resumes_at'] = alert_status.get('lesson_resumes_at')
        cfg['alert_duration'] = alert_status.get('alert_duration')

    res = apply_banner_stream(cfg)
    if not res.get('success'):
        return jsonify({
            'success': False,
            'error': res.get('error', 'Failed to generate stream')
        }), 500

    return jsonify({
        'success': True,
        'message': 'Заставку успішно згенеровано та потік перезавантажено!',
        'camera_reload': res.get('camera_reload')
    })


@app.route('/api/meet/air-alert/status')
@login_required
def api_meet_air_alert_status():
    """Get air alert monitoring state and Kyiv City alert status."""
    return jsonify({
        'success': True,
        'status': air_alert.get_status()
    })


@app.route('/api/meet/air-alert/toggle-monitoring', methods=['POST'])
@login_required
def api_meet_air_alert_toggle_monitoring():
    """Toggle master automated air alert monitoring."""
    req_data = request.get_json(silent=True) or {}
    enabled = req_data.get('enabled')
    if enabled is None:
        current = air_alert.get_status()
        enabled = not current.get('monitoring_enabled', True)
    res = air_alert.set_monitoring(enabled)
    return jsonify({'success': True, 'status': res})


@app.route('/api/meet/air-alert/trigger', methods=['POST'])
@login_required
def api_meet_air_alert_trigger():
    """Manually trigger alert (red or yellow)."""
    req_data = request.get_json(silent=True) or {}
    level = req_data.get('level', 'red')
    res = air_alert.trigger_manual_alert(level)
    return jsonify({'success': True, 'status': res})


@app.route('/api/meet/air-alert/clear', methods=['POST'])
@login_required
def api_meet_air_alert_clear():
    """Manually trigger all-clear (відбій)."""
    res = air_alert.trigger_manual_clear()
    return jsonify({'success': True, 'status': res})


@app.route('/api/meet/air-alert/reset-auto', methods=['POST'])
@login_required
def api_meet_air_alert_reset_auto():
    """Reset manual override and return to auto-monitoring."""
    res = air_alert.reset_to_auto()
    return jsonify({'success': True, 'status': res})


@app.route('/api/meet/air-alert/reset-banner', methods=['POST'])
@login_required
def api_meet_air_alert_reset_banner():
    """Remove alert overlay from stream banner and restore normal poster."""
    res = air_alert.reset_banner_to_normal()
    return jsonify({'success': True, 'status': res})


@app.route('/api/meet/alarm/test', methods=['POST'])
@login_required
def api_meet_alarm_test():
    """Test play alarm sound on/off in Google Meet."""
    data = request.get_json(silent=True) or {}
    sound_type = data.get('type', 'on')
    status_code, res = play_meet_alarm(sound_type)
    return jsonify({'success': status_code == 200, 'response': res})


@app.route('/api/meet/internal/on-joined', methods=['POST'])
def api_meet_internal_on_joined():
    """Internal webhook called by meet-streamer when bot enters Google Meet."""
    air_alert.on_meeting_joined()
    return jsonify({'success': True})



# ==========================================
# SCHOOL SERVICES APIS (MOODLE, JOBE, ROSTER, IDEA FACTORY)
# ==========================================

SCHOOL_CACHE = {
    'moodle_volume_size': '1.2G',
    'roster_db_size': '31M',
    'timestamp': 0,
    'updating': False
}

def update_school_storage_cache_async():
    """Update Moodle and Roster disk sizes in background thread."""
    if SCHOOL_CACHE.get('updating'):
        return

    def _worker():
        SCHOOL_CACHE['updating'] = True
        try:
            cmd = 'sh -c "docker exec services-moodle-1 du -sh /bitnami/moodledata 2>/dev/null; ls -lh /root/services/roster/data/db.sqlite3 2>/dev/null"'
            res = exec_host_command(cmd, timeout=30)
            if res.get('success'):
                lines = [l.strip() for l in res['output'].splitlines() if l.strip() and not l.startswith('**')]
                if len(lines) >= 1:
                    SCHOOL_CACHE['moodle_volume_size'] = lines[0].split()[0]
                if len(lines) >= 2:
                    p = lines[1].split()
                    if len(p) >= 5:
                        SCHOOL_CACHE['roster_db_size'] = p[4]
                SCHOOL_CACHE['timestamp'] = time.time()
        except Exception as e:
            print(f"Error checking school storage sizes: {e}")
        finally:
            SCHOOL_CACHE['updating'] = False

    t = threading.Thread(target=_worker, name="SchoolStorageWorker", daemon=True)
    t.start()


@app.route('/api/school/status')
@login_required
def api_school_status():
    """Get status of School services: Moodle, Jobe, Roster, Idea Factory."""
    # 1. Jobe
    jobe_online = False
    jobe_langs = []
    try:
        j_code, j_data = http_request('http://jobeserver/jobe/index.php/restapi/languages', timeout=3)
        if j_code == 200 and isinstance(j_data, list):
            jobe_online = True
            jobe_langs = [item[0] for item in j_data if isinstance(item, list) and len(item) > 0]
    except Exception:
        pass

    # 2. Roster
    roster_online = False
    try:
        r_code, _ = http_request('http://roster:8000/', headers={'Host': 'students.rmn.pp.ua'}, timeout=3)
        roster_online = (r_code == 200)
    except Exception:
        pass

    # 3. Idea Factory
    idea_online = False
    try:
        i_code, _ = http_request('http://idea_factory:5001/', timeout=3)
        idea_online = (i_code == 200)
    except Exception:
        pass

    # 4. Moodle HTTP & DB stats
    moodle_online = False
    moodle_stats = {
        'courses_count': 0,
        'users_count': 0
    }
    try:
        m_code, _ = http_request('http://services-moodle-1:8080/', headers={'Host': 'moodle.rmn.pp.ua'}, timeout=4)
        moodle_online = (m_code == 200)
    except Exception:
        pass

    # Query Moodle MariaDB for basic stats (courses, users)
    try:
        sql = (
            "SELECT count(*) FROM mdl_course WHERE id > 1; "
            "SELECT count(*) FROM mdl_user WHERE deleted = 0 AND id > 1;"
        )
        cmd = f"docker exec -i services-mariadb-1 mariadb -u bn_moodle bitnami_moodle -sN -e \"{sql}\""
        res = exec_host_command(cmd, timeout=5)
        if res.get('success'):
            lines = [l.strip() for l in res['output'].splitlines() if l.strip() and not l.startswith('**')]
            if len(lines) >= 2:
                moodle_stats['courses_count'] = int(lines[0])
                moodle_stats['users_count'] = int(lines[1])
    except Exception as e:
        print(f"Error querying Moodle stats: {e}")

    # Refresh storage sizes in background if older than 5 minutes
    if time.time() - SCHOOL_CACHE['timestamp'] > 300:
        update_school_storage_cache_async()

    return jsonify({
        'success': True,
        'moodle': {
            'online': moodle_online,
            'url': 'https://moodle.rmn.pp.ua',
            'courses_count': moodle_stats['courses_count'],
            'users_count': moodle_stats['users_count'],
            'volume_size': SCHOOL_CACHE['moodle_volume_size']
        },
        'jobe': {
            'online': jobe_online,
            'languages': jobe_langs,
            'url': 'https://jobe.rmn.pp.ua'
        },
        'roster': {
            'online': roster_online,
            'url': 'https://students.rmn.pp.ua',
            'db_size': SCHOOL_CACHE['roster_db_size']
        },
        'idea_factory': {
            'online': idea_online,
            'url': 'https://ideas.rmn.pp.ua',
            'openai_billing': get_openai_billing()
        }
    })


# ==========================================
# SYNCTHING & OBSIDIAN SYNC APIS
# ==========================================

@app.route('/api/syncthing/status')
@login_required
def api_syncthing_status():
    """Get status of Syncthing and Obsidian sync conflicts."""
    st_online = False
    try:
        code, _ = http_request('http://syncthing:8384/', timeout=3)
        st_online = (code == 200)
    except Exception:
        pass

    vault_size = "--"
    conflict_files = []
    try:
        cmd = "sh -c \"du -sh /root/services/obsidian-vault-personal 2>/dev/null; find /root/services/obsidian-vault-personal -name '*.sync-conflict-*' ! -path '*/.stversions/*' 2>/dev/null\""
        res = exec_host_command(cmd, timeout=5)
        if res.get('success'):
            lines = [l.strip() for l in res['output'].splitlines() if l.strip() and not l.startswith('**')]
            if lines:
                parts = lines[0].split()
                if parts:
                    vault_size = parts[0]
                conflict_files = [p.replace('/root/services/obsidian-vault-personal/', '') for p in lines[1:] if p]
    except Exception as e:
        print(f"Error checking Syncthing status: {e}")

    return jsonify({
        'success': True,
        'online': st_online,
        'url': 'https://syncthing.rmn.pp.ua',
        'vault_size': vault_size,
        'conflicts_count': len(conflict_files),
        'conflicts': conflict_files
    })


# ==========================================
# VAULTWARDEN (BITWARDEN) APIS
# ==========================================

@app.route('/api/vaultwarden/status')
@login_required
def api_vaultwarden_status():
    """Get Vaultwarden (Bitwarden) health, db size, and last backup info."""
    bw_online = False
    try:
        code, _ = http_request('http://services-bitwarden-1/alive', timeout=3)
        bw_online = (code == 200)
    except Exception:
        pass

    db_size = "--"
    last_backup = None
    try:
        cmd = 'sh -c "ls -lh /root/services/bw-data/db.sqlite3 2>/dev/null; ls -1t /root/services/obsidian-vault-personal/backups/vaultwarden/bw-data-backup* /root/services/backups/bw-data-backup* /root/services/bw-data-backup* 2>/dev/null | head -n 1 | xargs -r ls -lh"'
        res = exec_host_command(cmd, timeout=5)
        if res.get('success'):
            lines = [l.strip() for l in res['output'].splitlines() if l.strip() and not l.startswith('**')]
            if len(lines) >= 1:
                p0 = lines[0].split()
                if len(p0) >= 5:
                    db_size = p0[4]
            if len(lines) >= 2:
                p1 = lines[1].split()
                if len(p1) >= 9:
                    b_size = p1[4]
                    b_date = f"{p1[5]} {p1[6]} {p1[7]}"
                    b_name = os.path.basename(p1[8])
                    last_backup = f"{b_name} ({b_size}, {b_date})"
    except Exception as e:
        print(f"Error checking Vaultwarden status: {e}")

    return jsonify({
        'success': True,
        'online': bw_online,
        'url': 'https://bitwarden.rmn.pp.ua',
        'db_size': db_size,
        'last_backup': last_backup
    })


# ==========================================
# DOMAINS & CADDY CATALOG APIS
# ==========================================

@app.route('/api/domains/status')
@login_required
def api_domains_status():
    """Check health, HTTP code, and latency of all *.rmn.pp.ua subdomains."""
    domains = [
        {"name": "Moodle LMS", "domain": "moodle.rmn.pp.ua", "category": "School", "icon": "🎓", "desc": "Навчальна платформа Moodle"},
        {"name": "Students Roster", "domain": "students.rmn.pp.ua", "category": "School", "icon": "📋", "desc": "База учнівських списків"},
        {"name": "Jobe Sandbox", "domain": "jobe.rmn.pp.ua", "category": "School", "icon": "⚡", "desc": "Пісочниця CodeRunner"},
        {"name": "Idea Factory", "domain": "ideas.rmn.pp.ua", "category": "School", "icon": "💡", "desc": "Фабрика STEM-ідей"},
        {"name": "Vaultwarden", "domain": "bitwarden.rmn.pp.ua", "category": "Security", "icon": "🔐", "desc": "Менеджер паролів Bitwarden"},
        {"name": "Syncthing GUI", "domain": "syncthing.rmn.pp.ua", "category": "Sync", "icon": "🔄", "desc": "Синхронізація Obsidian сховища"},
        {"name": "CI/CD Manager", "domain": "cicd.rmn.pp.ua", "category": "Infra", "icon": "🛠", "desc": "Панель керування сервером"},
        {"name": "Light Bot Web", "domain": "light.rmn.pp.ua", "category": "Utilities", "icon": "💡", "desc": "Моніторинг відключень світла"},
        {"name": "Light My Fire", "domain": "fire.rmn.pp.ua", "category": "Services", "icon": "🔥", "desc": "Веб-сервіс LightMyFire"},
        {"name": "Bender", "domain": "bender.rmn.pp.ua", "category": "Infra", "icon": "🤖", "desc": "Внутрішній асистент"},
        {"name": "Monitorix", "domain": "monitorix.rmn.pp.ua", "category": "Monitoring", "icon": "📊", "desc": "Моніторинг системних ресурсів"},
        {"name": "Foocus AI", "domain": "foocus.rmn.pp.ua", "category": "AI", "icon": "🎨", "desc": "Генерація зображень Foocus"},
    ]

    results = []
    for d in domains:
        dom = d["domain"]
        cmd = f"curl -s -k -o /dev/null -w '%{{http_code}}:%{{time_total}}' --max-time 2.5 https://{dom}"
        res = exec_host_command(cmd, timeout=4)
        status_code = 0
        latency_ms = 0
        if res.get('success'):
            out = res.get('output', '').strip()
            out_lines = [l for l in out.splitlines() if not l.startswith('**')]
            out_val = out_lines[-1] if out_lines else ""
            if ':' in out_val:
                try:
                    c, t = out_val.split(':', 1)
                    status_code = int(c)
                    latency_ms = int(float(t) * 1000)
                except Exception:
                    pass
        results.append({
            **d,
            'url': f"https://{dom}",
            'status_code': status_code,
            'online': (status_code in [200, 301, 302, 401, 403]),
            'latency_ms': latency_ms
        })

    return jsonify({
        'success': True,
        'domains': results
    })


# ==========================================
# DOCKER COMPOSE MANAGEMENT (SERVICES)
# ==========================================

@app.route('/api/git-pull/<service>')
@login_required
def git_pull(service):
    username = session.get('username')
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    services = get_services()
    if service not in services:
        return jsonify({'success': False, 'output': 'Service not found'})
    
    service_info = services[service]
    if not service_info['build_context']:
        return jsonify({'success': False, 'output': 'Service has no build context'})
    
    service_path = os.path.join(SERVICES_DIR, service_info['build_context'])
    check_git = run_command('git rev-parse --is-inside-work-tree', cwd=service_path)
    if not check_git['success']:
        return jsonify({'success': False, 'output': 'Not a git repository'})
    
    result = run_command('git pull', cwd=service_path)
    return jsonify(result)


@app.route('/api/docker-build/<service>')
@login_required
def docker_build(service):
    username = session.get('username')
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    services = get_services()
    if service not in services:
        return jsonify({'success': False, 'output': 'Service not found'})
    
    service_info = services[service]
    if not service_info['has_build']:
        return jsonify({'success': False, 'output': 'Service has no build configuration'})
    
    command = f'{DOCKER_COMPOSE_CMD} -f {COMPOSE_FILE} build {service}'
    result = run_command(command)
    return jsonify(result)


@app.route('/api/docker-restart/<service>')
@login_required
def docker_restart(service):
    username = session.get('username')
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    command = f'{DOCKER_COMPOSE_CMD} -f {COMPOSE_FILE} restart {service}'
    result = run_command(command)
    return jsonify(result)


@app.route('/api/docker-up/<service>')
@login_required
def docker_up(service):
    username = session.get('username')
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    command = f'{DOCKER_COMPOSE_CMD} -f {COMPOSE_FILE} up -d {service}'
    result = run_command(command)
    return jsonify(result)


@app.route('/api/docker-status/<service>')
@login_required
def docker_status(service):
    username = session.get('username')
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    command = f'{DOCKER_COMPOSE_CMD} -f {COMPOSE_FILE} ps {service}'
    result = run_command(command)
    return jsonify(result)


@app.route('/api/docker-logs/<service>')
@login_required
def docker_logs(service):
    username = session.get('username')
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    command = f'{DOCKER_COMPOSE_CMD} -f {COMPOSE_FILE} logs --tail=150 {service}'
    result = run_command(command)
    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
