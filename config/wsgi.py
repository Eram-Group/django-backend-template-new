"""WSGI entrypoint (gunicorn). DJANGO_SETTINGS_MODULE is set by the runtime."""

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
