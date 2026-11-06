# Department Event Management Platform

A Flask + PostgreSQL web app for managing department seminars, workshops and student events:
add events, list events, and register students with a per-event **seat limit**.
It is packaged with Docker, deployed to Kubernetes (minikube), and automated with Ansible.

```
event-platform/
├── app/                 Flask app, requirements, Dockerfile
├── docker-compose.yml   Local test stack (app + postgres)
├── k8s/                 Namespace, ConfigMap, Secret, Postgres, App manifests
└── ansible/             inventory, setup.yml (tooling), deploy.yml (minikube deploy)
```

## Features
| Route | Purpose |
|---|---|
| `GET /` | List events with seats used/remaining and a register form |
| `GET/POST /events/add` | Create an event (category from `EVENT_CATEGORIES`, seat limit ≤ `MAX_REGISTRATIONS`) |
| `POST /events/<id>/register` | Register a student (blocked when full, duplicate email, or `REGISTRATION_OPEN=false`) |
| `GET /api/events` | JSON list of events |
| `POST /api/events/<id>/register` | JSON registration `{"name": "...", "email": "..."}` |
| `GET /api/config` | Shows the ConfigMap values the app is running with |
| `GET /health` | Readiness check (verifies DB connectivity) |

The seat limit is enforced inside a transaction with `SELECT ... FOR UPDATE`, so two replicas
can't oversell the last seat.

## Configuration
| Variable | Source | Example |
|---|---|---|
| `DEPT_NAME` | ConfigMap `event-config` | Department of Computer Science |
| `EVENT_CATEGORIES` | ConfigMap | Seminar,Workshop,Student Event,Hackathon |
| `MAX_REGISTRATIONS` | ConfigMap | 50 |
| `REGISTRATION_OPEN` | ConfigMap | true / false |
| `WELCOME_BANNER` | ConfigMap | Welcome! Register early... |
| `POSTGRES_DB/USER/PASSWORD` | Secret `db-credentials` | eventsdb / eventuser / eventpass |
| `DB_HOST`, `DB_PORT` | Deployment env | postgres / 5432 |

---

## 1. One-time setup (needs sudo)

```bash
sudo apt update && sudo apt install -y ansible
cd ansible
ansible-playbook setup.yml --ask-become-pass
newgrp docker        # or log out and back in, so docker works without sudo
docker ps            # should work without sudo now
```

## 2. Local test with Docker Compose (optional)

```bash
docker compose up --build
# open http://localhost:5000
docker compose down -v      # stop and delete the local DB volume
```

## 3. Deploy to minikube with Ansible

```bash
cd ansible
ansible-playbook deploy.yml
```
This starts minikube (docker driver), builds `event-app:1.0`, loads it into minikube,
applies every manifest in `k8s/`, waits for both rollouts and prints the service URL.

The app is also reachable at `http://$(minikube ip):30080`.

### Manual equivalent
```bash
minikube start --driver=docker
docker build -t event-app:1.0 app/
minikube image load event-app:1.0
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/
kubectl -n events rollout status deployment/postgres
kubectl -n events rollout status deployment/event-app
minikube service event-app -n events --url
```

## 4. Demo: change config without rebuilding
```bash
kubectl -n events edit configmap event-config      # e.g. REGISTRATION_OPEN: "false"
kubectl -n events rollout restart deployment/event-app
```
Refresh the page: the banner/state changes, and the header shows which pod served the request.

## Useful commands
```bash
kubectl -n events get all
kubectl -n events logs -l app=event-app --tail=50
kubectl -n events exec -it deploy/postgres -- psql -U eventuser -d eventsdb -c 'select * from registrations;'
kubectl delete namespace events        # tear down
minikube stop
```
