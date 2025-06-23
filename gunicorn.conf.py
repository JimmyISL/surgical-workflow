bind = "0.0.0.0:8000"
workers = 2
worker_class = "eventlet"
worker_connections = 1000
timeout = 30
max_requests = 1000
max_requests_jitter = 50