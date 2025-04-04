import pytest
from utils import is_loom_url

def test_is_loom_url():
    """Тест функции определения URL Loom"""
    # Проверка корректных URL Loom
    valid_loom_urls = [
        "https://www.loom.com/share/fc7d2e9bc1b74ce694b4efa33a92b065",
        "https://loom.com/share/fc7d2e9bc1b74ce694b4efa33a92b065",
        "https://www.loom.com/share/fc7d2e9bc1b74ce694b4efa33a92b065?t=6&sid=35bfbbb7-d3be-413a-8366-3c2b1368b632"
    ]
    for url in valid_loom_urls:
        assert is_loom_url(url) is True, f"URL {url} должен быть распознан как Loom"

    # Проверка некорректных URL
    invalid_loom_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://vimeo.com/123456789",
        "https://loom.com/not-share/something",
        "https://example.com",
        "not-a-url"
    ]
    for url in invalid_loom_urls:
        assert is_loom_url(url) is False, f"URL {url} не должен быть распознан как Loom"
