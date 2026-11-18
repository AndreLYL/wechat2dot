"""Queue-only stdio profile for native mode; no polling, callbacks or WeChat keys."""

from contextlib import closing

from wechat_dot_bridge.errors import BridgeError
from wechat_dot_bridge.mcp import MCPServer
from wechat_dot_bridge.sdk_adapter import SDKAdapter
from wechat_dot_bridge.store import Store

NAMES = {
    "send_weixin_notification",
    "get_weixin_notification_status",
    "set_weixin_notifications_paused",
}


class Notifications(SDKAdapter):
    async def _middleware(self, ctx, call_next):
        result = await super()._middleware(ctx, call_next)
        if isinstance(result, dict) and isinstance(result.get("capabilities"), dict):
            result["capabilities"].pop("events", None)
        return result

    async def _events(self, ctx, params):
        raise BridgeError("unknown_method")

    async def _list_tools(self, ctx, params):
        result = await super()._list_tools(ctx, params)
        result.tools = [t for t in result.tools if t.name in NAMES]
        return result

    async def _call_tool(self, ctx, params):
        if params.name not in NAMES:
            raise BridgeError("unknown_tool")
        return await super()._call_tool(ctx, params)


async def serve(root):
    from .config import Config

    cfg = Config.load(root)
    if cfg.mode != "native" or not cfg.room_id:
        raise ValueError("native_setup_required")
    with closing(Store(root / "state")) as store:
        await Notifications(MCPServer(store, None)).run_stdio()
