"""OS credential vault for desktop installations; owner-only files for containers."""

import hashlib
import json
from .dot.auth import TokenFile
from .paths import chatgpt_token_file


class VaultTokens(TokenFile):
    def __init__(self, path):
        super().__init__(path)
        self.service = (
            "wechat2dot." + hashlib.sha256(str(path.parent).encode()).hexdigest()[:18]
        )

    def load(self):
        import keyring

        v = keyring.get_password(self.service, "chatgpt")
        return json.loads(v) if v else None

    def save(self, tokens):
        import keyring

        keyring.set_password(self.service, "chatgpt", json.dumps(tokens))

    def clear(self):
        import keyring

        if keyring.get_password(self.service, "chatgpt") is not None:
            keyring.delete_password(self.service, "chatgpt")


def token_store(config, migrate=False):
    old = TokenFile(chatgpt_token_file())
    if config.credentials == "file":
        return old
    vault = VaultTokens(old.path)
    if migrate and old.path.exists():
        with old.locked():
            original = old.load()
            if original and not vault.load():
                vault.save(original)
            if original and vault.load() == original:
                old.clear()
    return vault


def protect_weixin(store, config):
    if config.credentials != "keyring":
        return
    import keyring

    b = store._binding()
    service = VaultTokens(store.directory.parent / "chatgpt-login.json").service
    if b["token"] != "@keyring":
        keyring.set_password(service, "weixin:" + b["id"], b["token"])
        if keyring.get_password(service, "weixin:" + b["id"]) != b["token"]:
            raise ValueError("keyring_write_failed")
        store.db.execute("UPDATE binding SET token='@keyring' WHERE id=?", (b["id"],))
