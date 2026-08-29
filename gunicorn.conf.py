"""gunicorn settings for the web task (0.25-0.5 vCPU Fargate tasks)."""

bind = "0.0.0.0:8000"
workers = 2
max_requests = 1000  # recycled workers guard against slow leaks
max_requests_jitter = 100
# A request may sit on a payment gateway for the whole outbound retry window
# (apps.common.http: 10 s x 3 attempts within 30 s). gunicorn's default 30 s
# SIGKILLed that worker mid-transaction; this stays above the window.
timeout = 60
graceful_timeout = 30
# Request logging is django-structlog's job (JSON, request_id, user_id, code
# for every request). gunicorn's plain-text access log duplicated each line
# in a format CloudWatch Insights cannot parse.
accesslog = None
