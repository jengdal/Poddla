import asyncio

from django.core.management.base import BaseCommand

from valkey_changes import valkey_client

_CACHE_KEY_PATTERN = "youtube:*"


class Command(BaseCommand):
    help = "Clear cached yt-dlp responses from Valkey"

    def handle(self, *args, **options):
        deleted = asyncio.run(self._clear())
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} cached entries."))

    async def _clear(self):
        vk = await valkey_client.get_client()
        keys = []
        cursor = "0"
        while True:
            cursor, batch = await vk.scan(cursor, match=_CACHE_KEY_PATTERN, count=100)
            keys.extend(batch)
            if cursor == b"0":
                break
        if keys:
            await vk.delete(keys)
        return len(keys)
