# Injector code from https://github.com/SteamDeckHomebrew/steamdeck-ui-inject. More info on how it works there.

from asyncio import sleep
from logging import getLogger
from traceback import format_exc

from aiohttp import ClientSession
from aiohttp.client_exceptions import ClientConnectorError

BASE_ADDRESS = "http://localhost:8080"

logger = getLogger("Injector")


class Tab:
    cmd_id = 0

    def __init__(self, res) -> None:
        self.title = res["title"]
        self.id = res["id"]
        self.ws_url = res["webSocketDebuggerUrl"]

        self.websocket = None
        self.client = None

    async def open_websocket(self):
        self.client = ClientSession()
        self.websocket = await self.client.ws_connect(self.ws_url)

    async def close_websocket(self):
        await self.client.close()

    async def listen_for_message(self):
        async for message in self.websocket:
            data = message.json()
            yield data

    async def _send_devtools_cmd(self, dc, receive=True):
        if self.websocket:
            self.cmd_id += 1
            dc["id"] = self.cmd_id
            await self.websocket.send_json(dc)
            if receive:
                async for msg in self.listen_for_message():
                    if "id" in msg and msg["id"] == dc["id"]:
                        return msg
            return None
        raise RuntimeError("Websocket not opened")

    async def evaluate_js(self, js, run_async=False, manage_socket=True, get_result=True):
        if manage_socket:
            await self.open_websocket()

        res = await self._send_devtools_cmd({
            "method": "Runtime.evaluate",
            "params": {
                "expression": js,
                "userGesture": True,
                "awaitPromise": run_async
            }
        }, get_result)

        if manage_socket:
            await self.close_websocket()
        return res

    async def enable(self):
        """
        Enables page domain notifications.
        """
        await self._send_devtools_cmd({
            "method": "Page.enable",
        }, False)

    async def disable(self):
        """
        Disables page domain notifications.
        """
        await self._send_devtools_cmd({
            "method": "Page.disable",
        }, False)

    async def reload_and_evaluate(self, js, manage_socket=True):
        """
        Reloads the current tab, with JS to run on load via debugger
        """
        if manage_socket:
            await self.open_websocket()

        await self._send_devtools_cmd({
            "method": "Debugger.enable"
        }, True)

        breakpoint_res = await self._send_devtools_cmd({
            "method": "Debugger.setInstrumentationBreakpoint",
            "params": {
                "instrumentation": "beforeScriptExecution"
            }
        }, True)

        logger.info(breakpoint_res)

        await self._send_devtools_cmd({
            "method": "Page.reload"
        }, True)

        # Page finishes loading when breakpoint hits

        for x in range(3):
            # this works around 1/2 of the time, so just send it 3 times.
            # the js accounts for being injected multiple times allowing only one instance to run at a time anyway
            await self._send_devtools_cmd({
                "method": "Runtime.evaluate",
                "params": {
                    "expression": js,
                    "userGesture": True,
                    "awaitPromise": False
                }
            }, False)

        await self._send_devtools_cmd({
            "method": "Debugger.removeBreakpoint",
            "params": {
                "breakpointId": breakpoint_res["result"]["breakpointId"]
            }
        }, True)

        await self._send_devtools_cmd({
            "method": "Debugger.resume"
        }, True)

        await self._send_devtools_cmd({
            "method": "Debugger.disable"
        }, True)

        if manage_socket:
            await self.close_websocket()
        return

    async def add_script_to_evaluate_on_new_document(self, js, add_dom_wrapper=True, manage_socket=True, get_result=True):
        """
        How the underlying call functions is not particularly clear from the devtools docs, so stealing puppeteer's description:

        Adds a function which would be invoked in one of the following scenarios:
        * whenever the page is navigated
        * whenever the child frame is attached or navigated. In this case, the
          function is invoked in the context of the newly attached frame.

        The function is invoked after the document was created but before any of
        its scripts were run. This is useful to amend the JavaScript environment,
        e.g. to seed `Math.random`.

        Parameters
        ----------
        js : str
            The script to evaluate on new document
        add_dom_wrapper : bool
            True to wrap the script in a wait for the 'DOMContentLoaded' event.
            DOM will usually not exist when this execution happens,
            so it is necessary to delay til DOM is loaded if you are modifying it
        manage_socket : bool
            True to have this function handle opening/closing the websocket for this tab
        get_result : bool
            True to wait for the result of this call

        Returns
        -------
        int or None
            The identifier of the script added, used to remove it later.
            (see remove_script_to_evaluate_on_new_document below)
            None is returned if `get_result` is False
        """

        wrappedjs = """
        function scriptFunc() {
            {js}
        }
        if (document.readyState === 'loading') {
            addEventListener('DOMContentLoaded', () => {
            scriptFunc();
        });
        } else {
            scriptFunc();
        }
        """.format(js=js) if add_dom_wrapper else js

        if manage_socket:
            await self.open_websocket()

        res = await self._send_devtools_cmd({
            "method": "Page.addScriptToEvaluateOnNewDocument",
            "params": {
                "source": wrappedjs
            }
        }, get_result)

        if manage_socket:
            await self.close_websocket()
        return res

    async def remove_script_to_evaluate_on_new_document(self, script_id, manage_socket=True):
        """
        Removes a script from a page that was added with `add_script_to_evaluate_on_new_document`

        Parameters
        ----------
        script_id : int
            The identifier of the script to remove (returned from `add_script_to_evaluate_on_new_document`)
        """

        if manage_socket:
            await self.open_websocket()

        res = await self._send_devtools_cmd({
            "method": "Page.removeScriptToEvaluateOnNewDocument",
            "params": {
                "identifier": script_id
            }
        }, False)

        if manage_socket:
            await self.close_websocket()

    async def get_steam_resource(self, url):
        res = await self.evaluate_js(f'(async function test() {{ return await (await fetch("{url}")).text() }})()', True)
        return res["result"]["result"]["value"]

    def __repr__(self):
        return self.title


async def get_tabs():
    async with ClientSession() as web:
        res = {}

        while True:
            try:
                res = await web.get(f"{BASE_ADDRESS}/json")
            except ClientConnectorError:
                logger.debug("ClientConnectorError excepted.")
                logger.debug("Steam isn't available yet. Wait for a moment...")
                logger.error(format_exc())
                await sleep(5)
            else:
                break

        if res.status == 200:
            r = await res.json()
            return [Tab(i) for i in r]
        else:
            raise Exception(f"/json did not return 200. {await res.text()}")


async def get_tab(tab_name):
    tabs = await get_tabs()
    tab = next((i for i in tabs if i.title == tab_name), None)
    if not tab:
        raise ValueError(f"Tab {tab_name} not found")
    return tab


async def inject_to_tab(tab_name, js, run_async=False):
    tab = await get_tab(tab_name)

    return await tab.evaluate_js(js, run_async)


async def tab_has_global_var(tab_name, var_name):
    try:
        tab = await get_tab(tab_name)
    except ValueError:
        return False
    res = await tab.evaluate_js(f"window['{var_name}'] !== null && window['{var_name}'] !== undefined", False)

    if not "result" in res or not "result" in res["result"] or not "value" in res["result"]["result"]:
        return False

    return res["result"]["result"]["value"]


async def tab_has_element(tab_name, element_name):
    try:
        tab = await get_tab(tab_name)
    except ValueError:
        return False
    res = await tab.evaluate_js(f"document.getElementById('{element_name}') != null", False)

    if not "result" in res or not "result" in res["result"] or not "value" in res["result"]["result"]:
        return False

    return res["result"]["result"]["value"]
