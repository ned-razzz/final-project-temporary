# final-project-temporary

## Server scaffolding

The server layer includes scaffolding for:

- `server/business_service`: asyncio TCP server
- `server/business_db`: MySQL database
- `server/control_service`: asyncio TCP server
- `server/vision_service`: asyncio TCP and UDP server
- `server/web_service`: FastAPI HTTP server and asyncio TCP server

TCP messages use newline-delimited JSON.

```json
{"type":"health"}
```

### Run with Docker Compose

```bash
docker compose up --build
```

### Check services

HTTP:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/status
```

TCP:

```bash
printf '{"type":"health"}\n' | nc localhost 9001
printf '{"type":"status"}\n' | nc localhost 9002
printf '{"type":"health"}\n' | nc localhost 9003
printf '{"type":"status"}\n' | nc localhost 9004
```

UDP:

```bash
printf '{"type":"health"}' | nc -u -w1 localhost 9103
```

MySQL:

```bash
docker exec -it business_db mysql -ubusiness_user -pbusiness_password business
```

### Ports

- BusinessService TCP: `9001`
- ControlService TCP: `9002`
- VisionService TCP: `9003`
- VisionService UDP: `9103`
- WebService HTTP: `8000`
- WebService TCP: `9004`
- BusinessDB MySQL: host `3307`, container `3306`
