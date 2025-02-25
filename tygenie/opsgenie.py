#!/usr/bin/env python3

import asyncio
import pendulum
from httpx import Timeout
from textual import log

import tygenie.consts as c

from tygenie.config import ty_config
from tygenie.opsgenie_rest_api_client import AuthenticatedClient
from tygenie.opsgenie_rest_api_client.models.add_tags_to_alert_payload import (
    AddTagsToAlertPayload,
)
from tygenie.opsgenie_rest_api_client.models.alert_action_payload import (
    AlertActionPayload,
)
from tygenie.opsgenie_rest_api_client.api.alert import (
    add_note,
    add_tags,
    close_alert,
    list_alerts,
    count_alerts,
    get_alert,
    list_notes,
    acknowledge_alert,
    remove_tags,
    un_acknowledge_alert,
)
from tygenie.opsgenie_rest_api_client.api.schedule import list_schedules
from tygenie.opsgenie_rest_api_client.api.who_is_on_call import (
    get_on_calls,
)
from tygenie.opsgenie_rest_api_client.api.account import get_info

__all__ = (
    "OpsGenie",
    "client",
)


def ApiLog(message: str = "") -> None:
    if not message:
        return

    log_config = ty_config.tygenie.get("log", {"enable": False})
    if "enable" in log_config and not log_config["enable"]:
        return

    date = pendulum.now()
    logline = f"[{date}] {message}"
    # Logline visble in textual console: textual console -vvv
    # and run app.py file with textual run app.py --dev
    log(f"{logline}")
    try:
        with open(log_config.get("file", "/tmp/tygenie.log"), "a") as f:
            f.write(logline + "\n")
    except Exception as e:
        log(f"Unable to log in file: {e}")
        pass


class OpsGenie:

    def __init__(self, api_key: str = "", host: str = "", username: str = ""):
        self.api_key = api_key
        self.host = host
        self.username = username
        self.source = "TyGenie {}".format(c.VERSION)
        self.client = AuthenticatedClient(
            base_url=self.host,
            token=self.api_key,
            prefix="GenieKey",
            timeout=Timeout(5.0, connect=10.0),
        )

    async def get_account_info(self):
        return await self.api_call(get_info)

    async def count_alerts(self, parameters: dict = {}):
        params = {"query": parameters.get("query", "")}
        return await self.api_call(count_alerts, **params)

    async def list_alerts(self, limit: int = 50, parameters: dict = {}):
        params = {"limit": limit, "sort": "updatedAt", "order": "desc", "query": ""}
        params.update(parameters)
        return await self.api_call(list_alerts, **params)

    async def get_alert(self, parameters: dict = {}):
        return await self.api_call(get_alert, **parameters)

    async def get_alert_notes(self, parameters: dict = {}):
        return await self.api_call(list_notes, **parameters)

    async def ack_alert(self, parameters: dict = {}, note: str = ""):
        body = AlertActionPayload(user=self.username, source=self.source, note=note)
        parameters["body"] = body
        return await self.api_call(acknowledge_alert, **parameters)

    async def add_note(self, parameters: dict = {}, note: str = ""):
        body = AlertActionPayload(user=self.username, source=self.source, note=note)
        parameters["body"] = body
        ApiLog(f"opsgenie call add_note with params: {parameters}")
        return await self.api_call(add_note, **parameters)

    async def unack_alert(self, parameters: dict = {}, note: str = ""):
        body = AlertActionPayload(user=self.username, source=self.source, note=note)
        parameters["body"] = body
        return await self.api_call(un_acknowledge_alert, **parameters)

    async def close_alert(self, parameters: dict = {}, note: str = ""):
        body = AlertActionPayload(
            user=self.username, source="TyGenie {}".format(c.VERSION), note=note
        )
        parameters["body"] = body
        return await self.api_call(close_alert, **parameters)

    async def tag_alert(
        self, parameters: dict = {}, tags: list[str] = [], note: str = ""
    ):
        body = AddTagsToAlertPayload(
            user=self.username, source=self.source, note=note, tags=tags
        )
        parameters["body"] = body
        return await self.api_call(add_tags, **parameters)

    async def remove_tag_alert(
        self, parameters: dict = {}, tags: list[str] = [], note: str = ""
    ):
        # There is no RemoveTagsToAlertPayload
        params = {
            "user": self.username,
            "source": self.source,
            "tags": tags,
            "note": note,
            "identifier": parameters["identifier"],
        }
        return await self.api_call(remove_tags, **params)

    async def list_schedules(self):
        return await self.api_call(list_schedules)

    async def whois_on_call(self, parameters: dict = {}):
        params = {"flat": True, "date": pendulum.now()} | parameters
        return await self.api_call(get_on_calls, **params)

    async def api_call(self, resource, **kwargs):

        response = None
        try:
            ApiLog(f"API call {resource.__name__} with params {kwargs}")
            response = await getattr(resource, "asyncio_detailed")(
                client=self.client, **kwargs
            )
            ApiLog(f"API status code: {response.status_code}")
            ApiLog(f"API content: {response.content}")
            ApiLog(f"API call {resource.__name__} done")
            return response.parsed
        except Exception as e:
            ApiLog(f"Exception in API call: {e}")
            return response


class Query:

    def __init__(self) -> None:
        self._limit: int = 22
        self.sort: str = "createdAt"
        self.order: str = "desc"
        self.query: str = "status:open"
        self.offset: int = 0
        self.current_filter: str | None = None
        self.current: dict = self.get()

    @property
    def limit(self) -> int:
        self._limit: int = int(ty_config.tygenie["alerts"].get("limit", 22))
        return self._limit

    @limit.setter
    def limit(self, value=0) -> int:
        self._limit: int = value or int(ty_config.tygenie["alerts"].get("limit", 22))
        return self._limit

    def _get_query(self, filter_name: str | None = None) -> str:
        query: str = ""
        if filter_name is None:
            if self.current_filter is not None:
                filter_name = self.current_filter
            else:
                filter_name = ty_config.tygenie.get("default_filter", None)

        if filter_name is not None:
            filters: dict = ty_config.tygenie.get("filters", {})
            cust_filter: dict | None = filters.get(filter_name, None)
            if cust_filter is None:
                ApiLog(f"Custom filter '{filter_name}' not found")
            else:
                query = cust_filter.get("filter", "")

        self.current_filter = filter_name

        return query

    def get(self, filter_name: str | None = None, parameters: dict = {}) -> dict:
        query: str = self._get_query(filter_name=filter_name)
        params: dict = {
            "limit": self.limit,
            "sort": self.sort,
            "order": self.order,
            "offset": self.offset,
            "query": query,
        }
        return params | parameters

    def current_page(self) -> int:
        return int(self.offset / self.limit) + 1

    def get_next(self) -> dict:
        self.offset += self.limit
        return self.get(parameters={"offset": self.offset})

    def get_previous(self) -> dict:
        self.offset -= self.limit
        return self.get(parameters={"offset": max(0, self.offset)})


class OpsgenieClient(OpsGenie):

    def __init__(self) -> None:
        self.api: OpsGenie = OpsGenie()
        self._load()
        super().__init__()

    def _load(self) -> None:
        self.api = OpsGenie(
            **{
                k: ty_config.opsgenie.get(k, None)
                for k in ["username", "host", "api_key"]
            }
        )

    def reload(self) -> None:
        self._load()


client = OpsgenieClient()

if __name__ == "__main__":

    async def main():
        task = asyncio.create_task(client.api.count_alerts())
        await task

    task = asyncio.run(main())
    print(task)
