from configuration.config import Config


class WEBSHARE:
    user = Config.WEBSHARE_PROXY_USER
    password = Config.WEBSHARE_PROXY_PASSWORD
    host = Config.WEBSHARE_PROXY_HOST
    port = Config.WEBSHARE_PROXY_PORT

    def get_proxy(self):
        """
           Returns a Proxy to object.
        """
        proxy = {
            'http': f"http://{self.user}:{self.password}@{self.host}:{self.port}",
            'https': f"http://{self.user}:{self.password}@{self.host}:{self.port}",
        }
        return proxy

