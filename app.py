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
from datetime import datetime, timezone
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
@login_required
def dashboard():
    """Display modern dashboard with tabs and services."""
    username = session.get('username')
    all_services = get_services()
    allowed_service_names = get_user_services(username)
    
    user_services = {name: info for name, info in all_services.items() 
                     if name in allowed_service_names}
    
    return render_template('dashboard.html', 
                           services=user_services, 
                           compose_file=COMPOSE_FILE,
                           username=username)


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
        # Parse ISO timestamp if embedded: "Last updated: 2026-10-08T14:14:42.930390+03:00"
        loc['stale'] = False
        loc['age_seconds'] = None
        loc['age_display'] = 'Невідомо'
        try:
            if 'Last updated: ' in last_str:
                iso_ts = last_str.replace('Last updated: ', '').strip()
                dt = datetime.fromisoformat(iso_ts)
                diff = (now - dt).total_seconds()
                loc['age_seconds'] = int(diff)
                if diff > 7200: # > 2 hours
                    loc['stale'] = True
                
                if diff < 60:
                    loc['age_display'] = 'Щойно (< 1 хв)'
                elif diff < 3600:
                    loc['age_display'] = f'{int(diff // 60)} хв тому'
                elif diff < 86400:
                    loc['age_display'] = f'{int(diff // 3600)} год тому'
                else:
                    loc['age_display'] = f'{int(diff // 86400)} дн тому'
        except Exception:
            pass

    return jsonify({'success': True, 'locations': locations})


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
# PO2BOT APIS
# ==========================================

@app.route('/api/po2bot/status')
@login_required
def api_po2bot_status():
    """Proxy Po2Bot status and pending count."""
    status_code, data = http_request(f'{PO2BOT_URL}/status', timeout=4)
    if status_code != 200:
        return jsonify({'success': False, 'status': 'offline', 'error': str(data)}), 200
    
    if isinstance(data, dict):
        data['success'] = True
        return jsonify(data)
    return jsonify({'success': False, 'status': 'offline'})


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
        data={'message': message},
        timeout=10
    )
    return jsonify({
        'success': status_code == 200 and isinstance(resp, dict) and resp.get('status') == 'ok',
        'response': resp
    })


@app.route('/api/meet/camera', methods=['POST'])
@login_required
def api_meet_camera_toggle():
    """Toggle camera in meeting."""
    status_code, resp = http_request(f'{MEET_STREAMER_URL}/camera/toggle', method='POST', timeout=10)
    return jsonify({
        'success': status_code == 200 and isinstance(resp, dict) and resp.get('status') == 'ok',
        'response': resp
    })


@app.route('/api/meet/leave', methods=['POST'])
@login_required
def api_meet_leave():
    """Leave current meeting."""
    status_code, resp = http_request(f'{MEET_STREAMER_URL}/leave', method='POST', timeout=10)
    return jsonify({
        'success': status_code == 200,
        'response': resp
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
