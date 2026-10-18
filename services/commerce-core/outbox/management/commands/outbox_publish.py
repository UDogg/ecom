import json
import signal
import time

from django.core.management.base import BaseCommand

from outbox.relay import LogSink, relay_batch


class Command(BaseCommand):
    help = "Relay unpublished outbox events. The RabbitMQ sink arrives with the outbox-publisher service."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Drain one batch and exit")
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--interval", type=float, default=1.0, help="Seconds between polls")

    def handle(self, once, batch_size, interval, **options):
        sink = LogSink(lambda envelope: self.stdout.write(json.dumps(envelope)))
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(True))
        while not stop:
            published = relay_batch(sink, batch_size=batch_size)
            if once:
                self.stderr.write(f"published {published}")
                return
            if published < batch_size:
                time.sleep(interval)
