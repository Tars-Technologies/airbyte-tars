import scrapy

from bs4 import BeautifulSoup
from typing import Optional
from scrapy.http import Response
from scrapy.spiders import SitemapSpider as ScrapySitemapSpider
from fake_useragent import UserAgent
from airbyte_cdk.logger import init_logger

from ..middleware.pdf import PdfDownloadMiddleware
from ..constants import ALLOWED_FILE_TYPE_MAP
from ..utils.format_cookie import format_cookies

logger = init_logger("airbyte")


class SitemapSpider(ScrapySitemapSpider):
    name = "sitemap_spider"
    auth_cookies: Optional[dict[str, str]] = None
    custom_settings = {
        "USER_AGENT": UserAgent(
            platforms=["pc"],
            browsers=["safari", "chrome"],
        ).random,
        "FEEDS": {
            "storage/exports/%(data_resource_id)s.csv": {
                "format": "csv",
                "fields": ["content", "source"],
            },
            "storage/exports/raw_%(data_resource_id)s.csv": {
                "format": "csv",
            },
        },
    }

    def __init__(
        self,
        url: str,
        data_resource_id: str,
        allowed_extensions: list,
        auth: Optional[dict[str, any]],
        *args,
        **kwargs,
    ):
        super(SitemapSpider, self).__init__(*args, **kwargs)
        self.sitemap_urls = [url]
        self.data_resource_id = data_resource_id
        self.allowed_extensions = allowed_extensions
        self.auth = auth
        if self.auth:
            auth_types = self.auth.get("type")
            if "microsoft" in auth_types:
                self.auth_cookies = format_cookies(self.auth.get("cookies"))
                logger.info(f"Auth cookies: {self.auth_cookies}")

    def start_requests(self):
        for url in self.sitemap_urls:
            yield scrapy.Request(
                url,
                cookies=self.auth_cookies if self.auth_cookies else None,
                callback=self._parse_sitemap,
            )

    def _parse_sitemap(self, response):
        response = response.replace(body=self._get_sitemap_body(response))
        for request in super()._parse_sitemap(response):
            yield request.replace(
                callback=self.parse,
                cookies=self.auth_cookies if self.auth_cookies else None,
            )

    def get_clean_content(self, response):
        soup = BeautifulSoup(response.text, "html.parser")
        clean_content = " \n ".join(" ".join(x.split()) for x in soup.get_text(separator=" ", strip=True).splitlines() if x.strip())
        return clean_content

    def get_pdf_content(self, response):
        pdf_download_middleware = PdfDownloadMiddleware()
        return pdf_download_middleware.process_response(response)

    def can_crawl(self, response: Response) -> bool:
        content_type = response.headers.get("Content-Type") or b""
        content_type = content_type.decode("utf-8")
        return (
            content_type.startswith("text/html")
            or content_type.startswith("text/xml")
            or content_type.startswith("application/xml")
            or content_type.startswith("application/pdf")
        )

    def is_pdf_document(self, response: Response) -> bool:
        content_type = response.headers.get("Content-Type") or b""
        return content_type.decode("utf-8").startswith("application/pdf")

    def is_allowed_type(self, response: Response) -> bool:
        content_type = response.headers.get("Content-Type") or b""
        content_type = content_type.decode("utf-8")
        return any(content_type.startswith(ALLOWED_FILE_TYPE_MAP[ext]) for ext in self.allowed_extensions)

    def parse(self, response: Response, **_):
        logger.info(f"Processing using sitemap scraper {response.url}")
        content = ""
        if self.can_crawl(response):
            if self.is_pdf_document(response) and self.is_allowed_type(response):
                content = self.get_pdf_content(response)
            elif self.is_allowed_type(response):
                content = self.get_clean_content(response)
            yield {
                "raw": response.body,
                "content": content,
                "source": response.url,
            }
