# CI/CD Manager

A simple web-based CI/CD manager for Docker Compose services with authentication.

**⚠️ Important:** This tool strictly manages services defined in `~/services/docker-compose.yml`. It does NOT manage standalone Docker containers - only Docker Compose services.

## Features

- 🔐 **Secure Login** - Username/password authentication
- 📥 **Git Pull** - Pull latest code from git repositories
- 🔨 **Docker Build** - Build Docker images for services
- 🔄 **Docker Restart** - Restart running services
- ▶️ **Docker Up** - Start services with docker compose up
- 📊 **Status Check** - View service status
- 🎨 **Modern UI** - Beautiful, responsive web interface

## Quick Start

### 1. Build and Start the Service

From the `/root/services` directory:

```bash
docker compose build cicd-manager
docker compose up -d cicd-manager
```

### 2. Access the Web UI

Open your browser and navigate to:
```
http://localhost:8080
```

Or if you're accessing from another machine:
```
http://YOUR_SERVER_IP:8080
```

### 3. Login

Default credentials:
- **Username:** `admin`
- **Password:** `admin`

**⚠️ IMPORTANT:** Change these credentials immediately in production!

## Configuration

### Managing Users

Users are managed via the `cicd-manager/users.json` file. Edit this file to add/remove users and configure their service access.

**Note:** `users.json` is gitignored to prevent committing passwords. Use `users.json.example` as a template:

```json
{
  "users": [
    {
      "username": "admin",
      "password": "your-secure-password",
      "services": ["*"]
    },
    {
      "username": "developer1",
      "password": "dev-password",
      "services": ["light-bot", "light-bot-staging"]
    },
    {
      "username": "bot-manager",
      "password": "bot-password",
      "services": ["po2bot", "po2bot-staging", "safemoodbot"]
    }
  ]
}
```

**Service Access:**
- `["*"]` - Access to ALL services (admin)
- `["service1", "service2"]` - Access to specific services only

After editing `users.json`, restart the service:
```bash
docker compose restart cicd-manager
```

**Note:** The ADMIN_USERNAME and ADMIN_PASSWORD environment variables are only used as fallback if users.json fails to load.

### Port Configuration

If port 8080 is already in use, change it in `docker-compose.yml`:

```yaml
cicd-manager:
  ports:
    - "9090:8080"  # Change 9090 to your preferred port
```

## Using the Dashboard

The dashboard displays all services from `~/services/docker-compose.yml`. All operations are executed via `docker compose` commands targeting this specific compose file.

### Service Cards

Each service in your `docker-compose.yml` will appear as a card with available actions:

- **📥 Git Pull** - Pulls the latest code from the git repository (only for services with build context)
- **🔨 Build** - Builds the Docker image for the service
- **🔄 Restart** - Restarts the running container
- **▶️ Up** - Starts or recreates the container
- **📊 Status** - Shows the current status of the service

### Output Display

After running any action, the output will be displayed below the action buttons:
- ✅ Green border = Success
- ❌ Red border = Error

## Security Considerations

1. **Change default credentials** - Always use strong passwords in production
2. **Use HTTPS** - Consider putting this behind a reverse proxy (like Caddy) with SSL
3. **Restrict access** - Use firewall rules to limit access to the web UI
4. **Docker socket** - This app has access to the Docker socket for compose operations. Only allow trusted users.
5. **Scope limited** - All commands are restricted to `docker compose` operations on `~/services/docker-compose.yml`

## Reverse Proxy Setup (Optional)

To add HTTPS and domain access via Caddy, add this to your `Caddyfile`:

```
cicd.yourdomain.com {
    reverse_proxy cicd-manager:8080
}
```

Then update `docker-compose.yml` to remove the ports exposure:

```yaml
cicd-manager:
  # Remove or comment out the ports section
  # ports:
  #   - "8080:8080"
```

## Troubleshooting

### Container won't start
```bash
# Check logs
docker compose logs cicd-manager

# Rebuild the image
docker compose build --no-cache cicd-manager
docker compose up -d cicd-manager
```

### Git pull fails
- Ensure the service directory is a git repository
- Check if you have proper git credentials configured
- SSH keys may need to be mounted into the container

### Docker commands fail
- Verify the Docker socket is properly mounted
- Check container has permissions to access `/var/run/docker.sock`

## File Structure

```
cicd-manager/
├── app.py              # Main Flask application
├── Dockerfile          # Container configuration
├── requirements.txt    # Python dependencies
├── templates/
│   ├── base.html      # Base template with styles
│   ├── login.html     # Login page
│   └── dashboard.html # Main dashboard
└── README.md          # This file
```

## Technical Details

- **Framework:** Flask 3.0
- **Language:** Python 3.11
- **Container:** Docker
- **Dependencies:** PyYAML, Werkzeug
- **Scope:** Strictly `docker compose` commands for `~/services/docker-compose.yml`
- **No standalone Docker:** This tool does NOT manage individual Docker containers, images, or networks

## License

This is a simple internal tool. Use at your own risk.

