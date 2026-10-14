import unittest

from vikunja import VikunjaClient, normalize_api_url, web_base_url


class VikunjaClientTests(unittest.TestCase):
    def test_normalizes_site_url(self):
        self.assertEqual(
            normalize_api_url("https://todo.example.com/"),
            "https://todo.example.com/api/v1",
        )

    def test_preserves_api_url(self):
        self.assertEqual(
            normalize_api_url("https://todo.example.com/api/v1"),
            "https://todo.example.com/api/v1",
        )

    def test_rejects_relative_url(self):
        with self.assertRaises(ValueError):
            normalize_api_url("todo.example.com")

    def test_board_links_use_the_browser_url(self):
        api = normalize_api_url("https://vkj.example.com/")
        self.assertEqual(web_base_url(api), "https://vkj.example.com")
        self.assertEqual(
            web_base_url(normalize_api_url("https://host/vikunja")),
            "https://host/vikunja",
        )
        self.assertEqual(web_base_url(""), "")

    def test_internal_api_and_private_browser_url_are_independent(self):
        client = VikunjaClient(
            "http://vikunja:3456", "token", web_url="https://astrbot.tailnet.ts.net/"
        )
        self.assertEqual(client.base_url, "http://vikunja:3456/api/v1")
        self.assertEqual(client.web_url, "https://astrbot.tailnet.ts.net")

    def test_empty_browser_url_preserves_existing_behavior(self):
        client = VikunjaClient("https://todo.example.com/subpath", "token")
        self.assertEqual(client.web_url, "https://todo.example.com/subpath")

    def test_invalid_browser_url_is_rejected(self):
        with self.assertRaises(ValueError):
            VikunjaClient("http://vikunja:3456", "token", web_url="relative-url")


if __name__ == "__main__":
    unittest.main()
