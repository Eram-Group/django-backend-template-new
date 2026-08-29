"""gunicorn settings for the web task (0.25-0.5 vCPU Fargate tasks)."""

bind = "0.0.0.0:8000"
workers = 2
max_requests = 1000  # recycled workers guard against slow leaks
max_requests_jitter = 100
accesslog = "-"
