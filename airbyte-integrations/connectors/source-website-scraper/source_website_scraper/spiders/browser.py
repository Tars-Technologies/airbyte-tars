import scrapy
import playwright.async_api

from http.cookies import SimpleCookie
from typing import Optional
from urllib.parse import urlparse
from scrapy_playwright.page import PageMethod
from scrapy.http import Response
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from airbyte_cdk.logger import init_logger
from ..middleware.pdf import PdfDownloadMiddleware
from ..constants import ALLOWED_SCHEMES, NOT_ALLOWED_EXT, ALLOWED_FILE_TYPE_MAP

logger = init_logger("airbyte")


async def should_abort_request(request: playwright.async_api.Request):
    return request.resource_type in {"image", "font", "media", ""}


class BrowserSpider(scrapy.Spider):
    name = "browser_spider"
    auth_cookies: Optional[dict[str, str]] = None
    custom_settings = {
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 60000,  # 1 minute
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",  # can be chromium, firefox, or webkit
        "USER_AGENT": UserAgent(platforms=["pc"], browsers=["chrome"]).random,
        "FEEDS": {
            "storage/exports/%(data_resource_id)s.csv": {
                "format": "csv",
                "fields": ["content", "source"],
            },
            "storage/exports/raw_%(data_resource_id)s.csv": {
                "format": "csv",
            },
        },
        "PLAYWRIGHT_ABORT_REQUEST": should_abort_request,
    }

    def __init__(
        self,
        url: str,
        data_resource_id: str,
        allowed_extensions,
        allowed_domains: list,
        auth: Optional[dict[str, any]],
        follow_given_url_only: bool = False,
        *args,
        **kwargs,
    ):
        super(BrowserSpider, self).__init__(*args, **kwargs)
        self.start_urls = [url]
        self.data_resource_id = data_resource_id
        self.allowed_extensions = allowed_extensions
        self.allowed_domains = [urlparse(url).netloc, *allowed_domains]
        self.follow_given_url_only = follow_given_url_only
        self.auth = auth
        if self.auth:
            auth_types = self.auth.get("type")
            if "microsoft" in auth_types:
                self.auth_cookies = self.format_cookies(self.auth.get("cookies"))
                logger.info(f"Auth cookies: {self.auth_cookies}")

    def get_clean_content(self, response):
        soup = BeautifulSoup(response.text, "html.parser")
        clean_content = " \n ".join(" ".join(x.split()) for x in soup.get_text(separator=" ", strip=True).splitlines() if x.strip())
        return clean_content

    def start_requests(self):
        yield scrapy.Request(
            self.start_urls[0],
            cookies=self.auth_cookies if self.auth_cookies else None,
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    # PageMethod("content"),
                    PageMethod("wait_for_load_state", state="load", timeout=6000),
                    # PageMethod("wait_for_load_state", "domcontentloaded"),
                ],
            },
        )

    def format_cookies(self, cookies):
        cookie = SimpleCookie()
        cookie.load(cookies)
        final_cookies = {}
        for key, morsel in cookie.items():
            final_cookies[key] = morsel.value
        return final_cookies

    def get_pdf_content(self, response):
        pdf_download_middleware = PdfDownloadMiddleware()
        return pdf_download_middleware.process_response(response)

    def is_valid_link(self, link):
        url_parsed = urlparse(link)

        if url_parsed.scheme not in ALLOWED_SCHEMES:
            return False

        ext = url_parsed.path.split(".")[-1]
        if ext.lower() in NOT_ALLOWED_EXT:
            return False

        return True

    def should_follow_url(self, link) -> bool:
        if self.follow_given_url_only:
            return link.startswith(self.start_urls[0])
        return True

    def is_html_document(self, response: Response) -> bool:
        content_type = response.headers.get("Content-Type") or b""
        return content_type.decode("utf-8").startswith("text/html")

    def is_pdf_document(self, response: Response) -> bool:
        content_type = response.headers.get("Content-Type") or b""
        return content_type.decode("utf-8").startswith("application/pdf")

    def is_allowed_type(self, response: Response) -> bool:
        content_type = response.headers.get("Content-Type") or b""
        content_type = content_type.decode("utf-8")
        return any(content_type.startswith(ALLOWED_FILE_TYPE_MAP[ext]) for ext in self.allowed_extensions)

    def handle_error(self, failure):
        logger.error(f"Error: {failure.getErrorMessage()}, {failure.request.url}, {failure.value}, {failure.type}")

    def parse(self, response: Response, **_):
        logger.info(f"Processing with playwright browser {response.url} with {self.allowed_domains}, {self.allowed_extensions}")
        logger.info(f"Response headers: {response.headers}")

        if "login.microsoftonline" in response.url:
            logger.info(f"Microsoft login page, skipping")
            return

        if self.is_html_document(response) and self.is_allowed_type(response):
            logger.info(f"is html document")
            yield {
                "raw": response.text,
                "content": self.get_clean_content(response),
                "source": response.url,
            }

        if self.is_pdf_document(response) and self.is_allowed_type(response):
            logger.info(f"is pdf document")
            yield {
                "raw": response.body,
                "content": self.get_pdf_content(response),
                "source": response.url,
            }

        for link in response.css("a::attr(href)").getall():
            if not self.is_valid_link(link):
                continue

            if not self.should_follow_url(link):
                continue

            full_url = response.urljoin(link)
            yield scrapy.Request(
                full_url,
                cookies=self.auth_cookies if self.auth_cookies else None,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        # PageMethod("content"),
                        PageMethod("wait_for_load_state", state="load", timeout=6000),
                        # PageMethod("wait_for_load_state", "domcontentloaded"),
                    ],
                },
            )
