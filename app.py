"""
CI/CD Manager for Docker Compose Services

This application strictly manages services defined in ~/services/docker-compose.yml
All operations are docker-compose specific and limited to the services directory.
No standalone docker commands are executed - only docker compose commands.
"""

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import os
import subprocess
import yaml
import json
from functools import wraps
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret-key-in-production')

# Configuration - Strictly for ~/services docker-compose.yml
COMPOSE_FILE = '/services/docker-compose.yml'
SERVICES_DIR = '/services'
USERS_FILE = '/app/users.json'
DOCKER_COMPOSE_CMD = 'docker-compose'  # Use 'docker-compose' or 'docker compose'

def load_users():
    """Load users from JSON file"""
    try:
        with open(USERS_FILE, 'r') as f:
            return json.load(f).get('users', [])
    except Exception as e:
        print(f"Error loading users: {e}")
        # Fallback to environment variables
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
    
    # If user has wildcard access, return all services
    if '*' in allowed_services:
        return list(get_services().keys())
    
    # Return only services user has access to
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

def run_command(command, cwd=None):
    """
    Execute a shell command and return output.
    All commands are docker compose commands targeting ~/services/docker-compose.yml
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd or SERVICES_DIR,
            capture_output=True,
            text=True,
            timeout=300
        )
        return {
            'success': result.returncode == 0,
            'output': result.stdout + result.stderr,
            'returncode': result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'output': 'Command timed out after 5 minutes',
            'returncode': -1
        }
    except Exception as e:
        return {
            'success': False,
            'output': str(e),
            'returncode': -1
        }

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
    """
    Display dashboard with services user has access to from ~/services/docker-compose.yml
    """
    username = session.get('username')
    all_services = get_services()
    allowed_service_names = get_user_services(username)
    
    # Filter services to only show what user has access to
    user_services = {name: info for name, info in all_services.items() 
                     if name in allowed_service_names}
    
    return render_template('dashboard.html', services=user_services, compose_file=COMPOSE_FILE)

@app.route('/api/git-pull/<service>')
@login_required
def git_pull(service):
    username = session.get('username')
    
    # Check user access
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    services = get_services()
    
    if service not in services:
        return jsonify({'success': False, 'output': 'Service not found'})
    
    service_info = services[service]
    if not service_info['build_context']:
        return jsonify({'success': False, 'output': 'Service has no build context'})
    
    service_path = os.path.join(SERVICES_DIR, service_info['build_context'])
    
    # Check if we're inside a git repository (handles .git in parent directories)
    check_git = run_command('git rev-parse --is-inside-work-tree', cwd=service_path)
    if not check_git['success']:
        return jsonify({'success': False, 'output': 'Not a git repository'})
    
    result = run_command('git pull', cwd=service_path)
    return jsonify(result)

@app.route('/api/docker-build/<service>')
@login_required
def docker_build(service):
    username = session.get('username')
    
    # Check user access
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
    
    # Check user access
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    services = get_services()
    
    if service not in services:
        return jsonify({'success': False, 'output': 'Service not found'})
    
    command = f'{DOCKER_COMPOSE_CMD} -f {COMPOSE_FILE} restart {service}'
    result = run_command(command)
    return jsonify(result)

@app.route('/api/docker-up/<service>')
@login_required
def docker_up(service):
    username = session.get('username')
    
    # Check user access
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    services = get_services()
    
    if service not in services:
        return jsonify({'success': False, 'output': 'Service not found'})
    
    command = f'{DOCKER_COMPOSE_CMD} -f {COMPOSE_FILE} up -d {service}'
    result = run_command(command)
    return jsonify(result)

@app.route('/api/docker-status/<service>')
@login_required
def docker_status(service):
    username = session.get('username')
    
    # Check user access
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    services = get_services()
    
    if service not in services:
        return jsonify({'success': False, 'output': 'Service not found'})
    
    command = f'{DOCKER_COMPOSE_CMD} -f {COMPOSE_FILE} ps {service}'
    result = run_command(command)
    return jsonify(result)

@app.route('/api/docker-logs/<service>')
@login_required
def docker_logs(service):
    username = session.get('username')
    
    # Check user access
    if not can_access_service(username, service):
        return jsonify({'success': False, 'output': 'Access denied to this service'})
    
    services = get_services()
    
    if service not in services:
        return jsonify({'success': False, 'output': 'Service not found'})
    
    command = f'{DOCKER_COMPOSE_CMD} -f {COMPOSE_FILE} logs --tail=100 {service}'
    result = run_command(command)
    return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)

