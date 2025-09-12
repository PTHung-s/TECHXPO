# TECHXPO Production Deployment Guide

## 📋 Environment Setup

### 1. Environment File Configuration

Copy `.env.example` to `.env` và điền thông tin thực tế:

```bash
cp .env.example .env
nano .env  # hoặc vi .env
```

### 2. Required Environment Variables

#### 🔑 **API Keys (Bắt buộc):**
- `GOOGLE_API_KEY`: Google Gemini API key
- `DEEPGRAM_API_KEY`: Deepgram speech-to-text API key
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`: LiveKit server credentials

#### 🌐 **DuckDNS Configuration:**
- `DUCKDNS_TOKEN`: Token từ DuckDNS dashboard
- `DUCKDNS_SUBDOMAINS`: Danh sách subdomain (comma-separated)
- `KIOSK_HOST`: Domain cho kiosk interface
- `DASHBOARD_HOST`: Domain cho admin dashboard
- `CADDY_EMAIL`: Email cho Let's Encrypt SSL certificates

## 🚀 Deployment Commands

### 1. Build và Deploy
```bash
# Build image
docker-compose build

# Start services
docker-compose up -d

# Check logs
docker-compose logs -f

# Check status
docker-compose ps
```

### 2. Services được deploy:

#### 📱 **App Service** (Container: techxpo-app)
- **Function**: AI Agent + Web Interface
- **Port**: 8080 (internal)
- **Domain**: `ai-doctor.duckdns.org`
- **Environment**: `RUN_AGENT=1`, `RUN_DASHBOARD=0`

#### 📊 **Dashboard Service** (Container: techxpo-dashboard)
- **Function**: Admin Dashboard
- **Port**: 8090 (internal)
- **Domain**: `doctor-dashboard.duckdns.org`
- **Command**: `uvicorn Dashboard.server:app`

#### 🌐 **Caddy Proxy** (Container: techxpo-proxy)
- **Function**: Reverse proxy + SSL termination
- **Ports**: 80, 443 (external)
- **SSL**: Automatic Let's Encrypt certificates

#### 🦆 **DuckDNS Service** (Container: techxpo-duckdns)
- **Function**: Dynamic DNS updates
- **Frequency**: Keeps IP updated every 5 minutes

## 🔧 Configuration Details

### Volumes:
- `booking_data`: Appointment and hospital data
- `dashboard_data`: Dashboard-specific data
- `kiosk_data`: SQLite database and output files
- `caddy_data`: SSL certificates and Caddy data
- `caddy_config`: Caddy configuration cache

### Networks:
- `web`: Internal Docker network for service communication

### Health Checks:
- **App**: `curl http://localhost:8080/healthz`
- **Dashboard**: Available via Caddy proxy

## 🌍 Public Access URLs

After deployment:
- **Kiosk Interface**: https://ai-doctor.duckdns.org
- **Admin Dashboard**: https://doctor-dashboard.duckdns.org

## 🛠️ Maintenance Commands

```bash
# View logs
docker-compose logs app
docker-compose logs dashboard
docker-compose logs caddy

# Restart specific service
docker-compose restart app
docker-compose restart dashboard

# Update and redeploy
git pull
docker-compose build
docker-compose up -d

# Backup data
docker run --rm -v techxpo_kiosk_data:/data -v $(pwd):/backup alpine tar czf /backup/kiosk-data-backup.tar.gz /data

# Restore data
docker run --rm -v techxpo_kiosk_data:/data -v $(pwd):/backup alpine tar xzf /backup/kiosk-data-backup.tar.gz -C /
```

## 🔒 Security Notes

1. **SSL/TLS**: Automatic via Let's Encrypt
2. **Firewall**: Only ports 80, 443 exposed externally
3. **Internal Communication**: Services communicate via Docker network
4. **Environment Variables**: Keep `.env` file secure, don't commit to git

## 📊 Monitoring

- **Container Status**: `docker-compose ps`
- **Logs**: `docker-compose logs -f`
- **Resource Usage**: `docker stats`
- **Health Endpoints**: 
  - https://ai-doctor.duckdns.org/healthz
  - https://doctor-dashboard.duckdns.org/api/health (if available)

## 🐛 Troubleshooting

### Common Issues:

1. **DuckDNS not updating**: Check DUCKDNS_TOKEN and DUCKDNS_SUBDOMAINS
2. **SSL certificate issues**: Check CADDY_EMAIL and domain DNS resolution
3. **API errors**: Verify all API keys in .env file
4. **Container startup issues**: Check `docker-compose logs [service-name]`