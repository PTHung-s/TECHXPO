# Caddyfile Configuration Guide

## 📋 Chức năng chính

File `Caddyfile` cấu hình Caddy reverse proxy để:

### 🌐 **Domain Routing:**
- **ai-doctor.duckdns.org** → App container (port 8080)
- **doctor-dashboard.duckdns.org** → Dashboard container (port 8090)

### 🔒 **SSL/HTTPS:**
- ✅ Automatic Let's Encrypt certificates
- ✅ HTTP → HTTPS redirects
- ✅ HSTS headers
- ✅ Security headers

### 📊 **Logging:**
- Access logs: `/var/log/caddy/kiosk-access.log`
- Dashboard logs: `/var/log/caddy/dashboard-access.log`

## 🔧 Environment Variables

Caddyfile sử dụng các biến từ `.env`:

```properties
KIOSK_HOST=ai-doctor.duckdns.org
DASHBOARD_HOST=doctor-dashboard.duckdns.org
CADDY_EMAIL=pthung310106@gmail.com
```

## 🛡️ Security Features

### Headers được thêm:
- `Strict-Transport-Security`: Force HTTPS
- `X-Content-Type-Options`: Prevent MIME sniffing
- `X-Frame-Options`: Prevent clickjacking
- `X-XSS-Protection`: XSS protection
- `Referrer-Policy`: Control referrer info

### Proxy Headers:
- `X-Real-IP`: Client IP address
- `X-Forwarded-For`: Forwarded IP chain
- `X-Forwarded-Proto`: Original protocol (https)

## 📡 Routing Logic

### Kiosk Interface (`ai-doctor.duckdns.org`):
```
/api/* → app:8080 (API endpoints)
/healthz → app:8080 (Health check)
/* → app:8080 (Static files + web interface)
```

### Dashboard Interface (`doctor-dashboard.duckdns.org`):
```
/* → dashboard:8090 (All dashboard requests)
```

## 🚀 Features Enabled

- **HTTP/2 & HTTP/3**: Modern protocols
- **Gzip Compression**: Faster loading
- **Automatic HTTPS**: Let's Encrypt integration
- **Health Checks**: Built-in monitoring

## 🔐 Optional Basic Auth

Để bảo vệ dashboard, uncomment section này trong Caddyfile:

```caddyfile
basicauth {
    admin $2a$14$hashed_password_here
}
```

Generate password hash:
```bash
caddy hash-password --plaintext "your_password"
```

## 📝 Log Files

Access logs sẽ được lưu trong container:
- Kiosk: `/var/log/caddy/kiosk-access.log`
- Dashboard: `/var/log/caddy/dashboard-access.log`

Xem logs:
```bash
docker-compose exec caddy tail -f /var/log/caddy/kiosk-access.log
docker-compose exec caddy tail -f /var/log/caddy/dashboard-access.log
```

## 🛠️ Customization

### Thêm domain mới:
```caddyfile
new-domain.duckdns.org {
    tls {$CADDY_EMAIL}
    reverse_proxy service:port
}
```

### Thêm rate limiting:
```caddyfile
rate_limit {
    zone static_rate_limit {
        key {remote_host}
        window 1m
        events 60
    }
}
```

### Thêm file serving:
```caddyfile
handle /static/* {
    file_server
    root /var/www/static
}
```

## 🔍 Testing

Test cấu hình:
```bash
# Validate Caddyfile syntax
docker run --rm -v $(pwd)/Caddyfile:/etc/caddy/Caddyfile caddy:2 caddy validate

# Test domains resolve
nslookup ai-doctor.duckdns.org
nslookup doctor-dashboard.duckdns.org

# Test SSL
curl -I https://ai-doctor.duckdns.org
curl -I https://doctor-dashboard.duckdns.org
```

## 🐛 Troubleshooting

### Common issues:

1. **SSL certificate issues:**
   - Check CADDY_EMAIL is valid
   - Ensure domains resolve to your server IP
   - Check port 443 is accessible

2. **Domain not resolving:**
   - Verify DuckDNS subdomain setup
   - Check DUCKDNS_TOKEN and DUCKDNS_SUBDOMAINS

3. **Proxy errors:**
   - Check container names in docker-compose.yml
   - Verify services are running: `docker-compose ps`

### Debug commands:
```bash
# Check Caddy logs
docker-compose logs caddy

# Check container connectivity
docker-compose exec caddy ping app
docker-compose exec caddy ping dashboard

# Reload configuration
docker-compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```