#!/usr/bin/env python
"""Django's command-line utility. DJANGO_SETTINGS_MODULE is always explicit:
the justfile exports config.settings.local, pytest pins config.settings.test,
the image runs config.settings.production."""

import sys

from django.core.management import execute_from_command_line

if __name__ == "__main__":
    execute_from_command_line(sys.argv)
