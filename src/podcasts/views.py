import asyncio

from asgiref.sync import sync_to_async
from datastar_py import ServerSentEventGenerator
from datastar_py.django import DatastarResponse
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string


def render_index(request: HttpRequest):
    return render_to_string(
        request=request, template_name="podcasts/podcasts.html", context={}
    )


def podcasts(request: HttpRequest):
    return HttpResponse(render_index(request=request))


async def podcasts_sse(request: HttpRequest):
    async def generator():
        event_id = 0
        while True:
            event_id += 1
            html = await sync_to_async(render_index)(request=request)
            yield ServerSentEventGenerator.patch_elements(html, event_id=str(event_id))
            await asyncio.sleep(1)

    return DatastarResponse(content=generator())
