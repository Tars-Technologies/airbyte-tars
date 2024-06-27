import logging
import os
import fnmatch
import pytz
import uuid
import pandas as pd
import hashlib

from abc import ABC
from datetime import datetime
from twisted.internet import reactor
from multiprocessing import Process
from typing import Iterable, List, Mapping, Optional, Any, MutableMapping
from scrapy import signals
from scrapy.crawler import Crawler

from airbyte_cdk.sources import AbstractSource
from airbyte_cdk.models import (
    AirbyteMessage,
    AirbyteRecordMessage,
    ConfiguredAirbyteCatalog,
    SyncMode,
    Type,
    AirbyteLogMessage,
)
from airbyte_cdk.sources.streams import Stream, IncrementalMixin
from .spiders.web_base import WebBaseSpider
from .spiders.sitemap import SitemapSpider
from .spiders.browser import BrowserSpider


class Website(Stream, IncrementalMixin, ABC):
    primary_key = "source"
    state_checkpoint_interval = 100

    def __init__(self, config, name, cursor_field: Optional[Any] = None):
        super().__init__()
        self._name = (name,)
        self._cursor_field = cursor_field
        self.config = config
        self.data_dir = "storage/exports"
        self.url = config["url"]
        self.closespider_pagecount = config.get("closespider_pagecount", 100)
        self.allowed_mime_types = config.get("allowed_mime_types", ["html"])
        self.allowed_domains = config.get("allowed_domains", [])
        self.use_browser = config.get("use_browser", False)
        self.data_resource_id = config.get("data_resource_id", str(uuid.uuid4()))
        self.auth = config.get("auth", None)
        self._state = {}

    @property
    def state(self) -> MutableMapping[str, Any]:
        return self._state

    @state.setter
    def state(self, value: MutableMapping[str, Any]):
        self._state = value

    @property
    def cursor_field(self):
        return self._cursor_field

    def get_updated_state(
        self, current_stream_state: MutableMapping[str, Any], latest_record: Mapping[str, Any]
    ) -> MutableMapping[str, Any]:
        """
        Returns current state. At the moment when this method is called by sources we already have updated state stored in self._state,
        because it is calculated each time we produce new record
        """
        return self.state

    def generate_hash(self, content):
        if not isinstance(content, str):
            content = str(content)
        return hashlib.md5(content.encode("utf-8")).hexdigest()


    def run_spider(self):

        crawler = Crawler(
            WebBaseSpider,
            settings={
                "CLOSESPIDER_PAGECOUNT": self.closespider_pagecount,
            },
        )

        if self.use_browser is True:
            crawler = Crawler(
                BrowserSpider,
                settings={
                    "CLOSESPIDER_PAGECOUNT": self.closespider_pagecount,
                },
            )
        elif self.url.endswith("xml"):
            crawler = Crawler(
                SitemapSpider,
                settings={
                    "CLOSESPIDER_PAGECOUNT": self.closespider_pagecount,
                },
            )

        crawler.signals.connect(reactor.stop, signal=signals.spider_closed)  # type: ignore
        extra_args = {
            "url": self.url,
            "data_resource_id": self.data_resource_id,
            "allowed_extensions": self.allowed_mime_types,
            "allowed_domains": self.allowed_domains,
            "auth": self.auth,
        }
        crawler.crawl(**extra_args)
        reactor.run()  # type: ignore

    def read_records(
        self,
        sync_mode: SyncMode,
        logger: logging.Logger,
        cursor_field: Optional[List[str]] = None,
        stream_slice: Optional[Mapping[str, Any]] = None,
        stream_state: Optional[Mapping[str, Any]] = None,
    ) -> Iterable[AirbyteRecordMessage]:

        state_value = self.state.get(self.cursor_field)

        if stream_state:
            state_value = stream_state.get(self.cursor_field)
        cursor_value = state_value
        logger.info(f"State Value: {state_value}, Cursor Value: {cursor_value}")
        process = Process(target=self.run_spider)
        process.start()
        process.join()
        pattern = f"{self.data_resource_id}*.csv"
        for filename in os.listdir(self.data_dir):
            if fnmatch.fnmatch(filename, pattern):
                file_path = os.path.join(self.data_dir, filename)
                try:
                    df = pd.read_csv(file_path)
                    for _, row in df.iterrows():
                        content = row.get("content", "")
                        if not isinstance(content, str):
                            content = str(content)
                        content_hash = self.generate_hash(content)
                        transformed_row = {
                            "content": content,
                            "data_resource_id": self.data_resource_id,
                            "source": row.get("source"),
                            "content_hash": f"{row.get('source')}_{content_hash}",
                        }
                        record = AirbyteRecordMessage(
                            stream=self.name,  # Stream name
                            data=transformed_row,
                            emitted_at=int(datetime.now(tz=pytz.UTC).timestamp() * 1000),
                            meta={"file_path": file_path},
                        )
                        self.state.update({self.cursor_field: transformed_row.get("content_hash")})
                        logger.info(f"Final State: {self.state}")
                        yield record
                except Exception as e:
                    raise e

    def get_json_schema(self):
        # Return the JSON schema for this stream
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "source": {"type": "string"},
                "data_resource_id": {"type": "string"},
                "content_hash": {"type": "string"},
            },
        }


class SourceWebsiteScraper(AbstractSource):
    def check_connection(self, logger, config) -> str:
        # Check if the connection is valid
        return True, None

    def streams(self, config) -> Iterable[Stream]:
        # Return a list of streams
        return [Website(config, name="website", cursor_field="content_hash")]

    def read(
        self,
        logger: logging.Logger,
        config: Mapping[str, Any],
        catalog: ConfiguredAirbyteCatalog,
        state: MutableMapping[str, Any] = None,
    ) -> Iterable[AirbyteMessage]:
        stream = Website(config, name="website", cursor_field="content_hash")
        logger.info(f"Syncing stream: {stream.name}, {stream.config}, {state}")
        try:
            for record in stream.read_records(SyncMode.incremental, logger=logger, stream_state=state.get(stream.name, {})):
                logger.info(f"Syncing Record: {record}")
                yield AirbyteMessage(type=Type.RECORD, record=record)
        except Exception as e:
            logger.error(f"Failed to read stream {stream.name}: {repr(e)}")
            yield AirbyteMessage(
                type=Type.LOG, log=AirbyteLogMessage(level="FATAL", message=f"Failed to read stream {stream.name}: {repr(e)}")
            )
