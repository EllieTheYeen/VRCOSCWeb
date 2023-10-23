from pythonosc.udp_client import SimpleUDPClient
from pythonosc.dispatcher import Dispatcher
from pythonosc import osc_server
import quart

import traceback
import asyncio
import signal
import glob
import sys
import json
import os


class AsyncClient(SimpleUDPClient):
    def __init__(self, *a, loop: asyncio.AbstractEventLoop, **k):
        self.loop = loop
        SimpleUDPClient.__init__(self, *a, **k)

    async def send_message(self, addr, val):
        return await self.loop.run_in_executor(
            None, SimpleUDPClient.send_message, self, addr, val
        )


def handle(addr, msg):
    if addr == "/avatar/change":
        global avatar
        avatar = msg
        varstates.clear()
    elif addr.startswith("/avatar/parameter/"):
        varstates[addr] = msg.split("/", 3)[-1]

    for que in queues:
        try:
            que.put_nowait({"addr": addr, "msg": msg})
        except asyncio.QueueFull as e:
            traceback.print_exc()


dispatcher = Dispatcher()
dispatcher.map("*", handle)

app = quart.Quart(__name__)
curpath = os.path.split(__file__)[0] or "."


@app.route("/")
async def index():
    return await quart.templating.render_template("interface.html")


@app.route("/favicon.ico")
async def favicon():
    return await quart.helpers.send_file(os.path.join(curpath, "static/favicon.ico"))


@app.route("/avatar/<path:avatar>")
async def getavatar(avatar):
    base = os.path.expanduser("~/AppData/LocalLow/VRChat/VRChat/OSC")
    fname = f"{base}/*/Avatars/{avatar}.json"
    fname = glob.glob(fname)[0]
    return await quart.helpers.send_file(fname)


@app.websocket("/ws")
async def ws():
    webclient = quart.websocket._get_current_object()
    myqueue = asyncio.Queue()
    queues.add(myqueue)
    print(f"Con: {':'.join(map(str, webclient.scope['client']))}")
    try:
        task = asyncio.get_event_loop().create_task(pumptask(webclient, myqueue))
        if avatar:
            await webclient.send_json({"addr": "/avatar/change", "msg": avatar})
        for key, value in varstates.items():
            await webclient.send_json(
                {"addr": f"/avatar/parameters/{key}", "msg": value}
            )
        while True:
            raw = await webclient.receive()
            j = json.loads(raw)
            await oscclient.send_message(j["addr"], j["msg"])
    finally:
        print(f"Dis: {':'.join(map(str, webclient.scope['client']))}")
        queues.remove(myqueue)
        await webclient.close(0)
        task.cancel()


async def pumptask(ws: quart.Websocket, que: asyncio.Queue):
    while True:
        msg = await que.get()
        await ws.send_json(msg)
        que.task_done()


async def startosc():
    global oscclient, oscserver
    loop = asyncio.get_running_loop()
    oscclient = AsyncClient("127.0.0.1", 9000, loop=loop)
    oscserver = osc_server.AsyncIOOSCUDPServer(("127.0.0.1", 9001), dispatcher, loop)
    loop.create_task(oscserver.create_serve_endpoint())


queues: set[asyncio.Queue] = set()
avatar = None
varstates = {}  # TODO: Fix so this is initialized later in client

if __name__ == "__main__":
    import hypercorn
    import logging

    try:
        import coloredlogs

        print("Found coloredlogs. Using", file=sys.stderr)
        coloredlogs.install(logging.DEBUG)
    except Exception:
        pass

    def _signal_handler(*a) -> None:
        if not shutdown_event.is_set():
            print("Shutting down", file=sys.stderr)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGABRT, _signal_handler)

    config = hypercorn.Config()
    config.accesslog = "-"
    config.errorlog = "-"
    config.loglevel = "DEBUG"
    config._bind = ["127.0.0.1:4444"]
    # Uncomment to listen to all interfaces which is useful if you use this from another computer
    # config._bind = ["0.0.0.0:4444"]
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    async def run():
        global shutdown_event
        shutdown_event = asyncio.Event()
        await startosc()
        print("Started OSC server")
        await hypercorn.asyncio.serve(app, config, shutdown_trigger=shutdown_event.wait)

    asyncio.run(run())
