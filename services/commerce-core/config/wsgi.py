import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()

# Refuse to boot unguarded: the check runs at import, not on the first request.
from django.conf import settings  # noqa: E402

from common.gateway import check_configuration  # noqa: E402

check_configuration(settings.GATEWAY_SECRET, settings.GATEWAY_ALLOW_UNGUARDED)
