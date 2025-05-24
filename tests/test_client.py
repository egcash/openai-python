# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from __future__ import annotations

import gc
import os
import sys
import json
import time
import asyncio
import inspect
import subprocess
import tracemalloc
from typing import Any, Union, cast
from textwrap import dedent
from unittest import mock
from typing_extensions import Literal

import httpx
import pytest
from respx import MockRouter
from pydantic import ValidationError

from openai import OpenAI, AsyncOpenAI, APIResponseValidationError
from openai._types import Omit
from openai._utils import maybe_transform
from openai._models import BaseModel, FinalRequestOptions
from openai._constants import RAW_RESPONSE_HEADER
from openai._streaming import Stream, AsyncStream
from openai._exceptions import OpenAIError, APIStatusError, APITimeoutError, APIResponseValidationError
from openai._base_client import DEFAULT_TIMEOUT, HTTPX_DEFAULT_TIMEOUT, BaseClient, make_request_options
from openai.types.chat.completion_create_params import CompletionCreateParamsNonStreaming

from .utils import update_env

base_url = os.environ.get("TEST_API_BASE_URL", "http://127.0.0.1:4010")
api_key = "My API Key"


def _get_params(client: BaseClient[Any, Any]) -> dict[str, str]:
    request = client._build_request(FinalRequestOptions(method="get", url="/foo"))
    url = httpx.URL(request.url)
    return dict(url.params)


def _low_retry_timeout(*_args: Any, **_kwargs: Any) -> float:
    return 0.1


def _get_open_connections(client: OpenAI | AsyncOpenAI) -> int:
    transport = client._client._transport
    assert isinstance(transport, httpx.HTTPTransport) or isinstance(transport, httpx.AsyncHTTPTransport)

    pool = transport._pool
    return len(pool._requests)


class TestOpenAI:
    client = OpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

    @pytest.mark.respx(base_url=base_url)
    def test_raw_response(self, respx_mock: MockRouter) -> None:
        respx_mock.post("/foo").mock(return_value=httpx.Response(200, json={"foo": "bar"}))

        response = self.client.post("/foo", cast_to=httpx.Response)
        assert response.status_code == 200
        assert isinstance(response, httpx.Response)
        assert response.json() == {"foo": "bar"}

    @pytest.mark.respx(base_url=base_url)
    def test_raw_response_for_binary(self, respx_mock: MockRouter) -> None:
        respx_mock.post("/foo").mock(
            return_value=httpx.Response(200, headers={"Content-Type": "application/binary"}, content='{"foo": "bar"}')
        )

        response = self.client.post("/foo", cast_to=httpx.Response)
        assert response.status_code == 200
        assert isinstance(response, httpx.Response)
        assert response.json() == {"foo": "bar"}

    def test_copy(self) -> None:
        copied = self.client.copy()
        assert id(copied) != id(self.client)

        copied = self.client.copy(api_key="another My API Key")
        assert copied.api_key == "another My API Key"
        assert self.client.api_key == "My API Key"

    def test_copy_default_options(self) -> None:
        # options that have a default are overridden correctly
        copied = self.client.copy(max_retries=7)
        assert copied.max_retries == 7
        assert self.client.max_retries == 2

        copied2 = copied.copy(max_retries=6)
        assert copied2.max_retries == 6
        assert copied.max_retries == 7

        # timeout
        assert isinstance(self.client.timeout, httpx.Timeout)
        copied = self.client.copy(timeout=None)
        assert copied.timeout is None
        assert isinstance(self.client.timeout, httpx.Timeout)

    def test_copy_default_headers(self) -> None:
        client = OpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, default_headers={"X-Foo": "bar"}
        )
        assert client.default_headers["X-Foo"] == "bar"

        # does not override the already given value when not specified
        copied = client.copy()
        assert copied.default_headers["X-Foo"] == "bar"

        # merges already given headers
        copied = client.copy(default_headers={"X-Bar": "stainless"})
        assert copied.default_headers["X-Foo"] == "bar"
        assert copied.default_headers["X-Bar"] == "stainless"

        # uses new values for any already given headers
        copied = client.copy(default_headers={"X-Foo": "stainless"})
        assert copied.default_headers["X-Foo"] == "stainless"

        # set_default_headers

        # completely overrides already set values
        copied = client.copy(set_default_headers={})
        assert copied.default_headers.get("X-Foo") is None

        copied = client.copy(set_default_headers={"X-Bar": "Robert"})
        assert copied.default_headers["X-Bar"] == "Robert"

        with pytest.raises(
            ValueError,
            match="`default_headers` and `set_default_headers` arguments are mutually exclusive",
        ):
            client.copy(set_default_headers={}, default_headers={"X-Foo": "Bar"})

    def test_copy_default_query(self) -> None:
        client = OpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, default_query={"foo": "bar"}
        )
        assert _get_params(client)["foo"] == "bar"

        # does not override the already given value when not specified
        copied = client.copy()
        assert _get_params(copied)["foo"] == "bar"

        # merges already given params
        copied = client.copy(default_query={"bar": "stainless"})
        params = _get_params(copied)
        assert params["foo"] == "bar"
        assert params["bar"] == "stainless"

        # uses new values for any already given headers
        copied = client.copy(default_query={"foo": "stainless"})
        assert _get_params(copied)["foo"] == "stainless"

        # set_default_query

        # completely overrides already set values
        copied = client.copy(set_default_query={})
        assert _get_params(copied) == {}

        copied = client.copy(set_default_query={"bar": "Robert"})
        assert _get_params(copied)["bar"] == "Robert"

        with pytest.raises(
            ValueError,
            # TODO: update
            match="`default_query` and `set_default_query` arguments are mutually exclusive",
        ):
            client.copy(set_default_query={}, default_query={"foo": "Bar"})

    def test_copy_signature(self) -> None:
        # ensure the same parameters that can be passed to the client are defined in the `.copy()` method
        init_signature = inspect.signature(
            # mypy doesn't like that we access the `__init__` property.
            self.client.__init__,  # type: ignore[misc]
        )
        copy_signature = inspect.signature(self.client.copy)
        exclude_params = {"transport", "proxies", "_strict_response_validation"}

        for name in init_signature.parameters.keys():
            if name in exclude_params:
                continue

            copy_param = copy_signature.parameters.get(name)
            assert copy_param is not None, f"copy() signature is missing the {name} param"

    @pytest.mark.asyncio
    async def test_copy_build_request(self) -> None: # Made async
        options = FinalRequestOptions(method="get", url="/foo")

        async def build_request_async(options: FinalRequestOptions) -> None: # Made async
            client = self.client.copy()
            # Call the new aiohttp-focused method
            await client._build_aiohttp_request_kwargs_with_files(options)
            await client.close() # Ensure copied client session is closed

        # ensure that the machinery is warmed up before tracing starts.
        await build_request_async(options) # Made async
        gc.collect()

        tracemalloc.start(1000)

        snapshot_before = tracemalloc.take_snapshot()

        ITERATIONS = 10
        for _ in range(ITERATIONS):
            await build_request_async(options) # Made async

        gc.collect()
        snapshot_after = tracemalloc.take_snapshot()

        tracemalloc.stop()

        def add_leak(leaks: list[tracemalloc.StatisticDiff], diff: tracemalloc.StatisticDiff) -> None:
            if diff.count == 0:
                # Avoid false positives by considering only leaks (i.e. allocations that persist).
                return

            if diff.count % ITERATIONS != 0:
                # Avoid false positives by considering only leaks that appear per iteration.
                return

            for frame in diff.traceback:
                if any(
                    frame.filename.endswith(fragment)
                    for fragment in [
                        # to_raw_response_wrapper leaks through the @functools.wraps() decorator.
                        #
                        # removing the decorator fixes the leak for reasons we don't understand.
                        "openai/_legacy_response.py",
                        "openai/_response.py",
                        # pydantic.BaseModel.model_dump || pydantic.BaseModel.dict leak memory for some reason.
                        "openai/_compat.py",
                        # Standard library leaks we don't care about.
                        "/logging/__init__.py",
                        # Anyio path resolution can sometimes show up in CI, ignore it.
                        "/anyio/_core/_paths.py"
                    ]
                ):
                    return

            leaks.append(diff)

        leaks: list[tracemalloc.StatisticDiff] = []
        for diff in snapshot_after.compare_to(snapshot_before, "traceback"):
            add_leak(leaks, diff)
        if leaks:
            for leak in leaks:
                print("MEMORY LEAK:", leak)
                for frame in leak.traceback:
                    print(frame)
            raise AssertionError()

    @pytest.mark.asyncio
    async def test_request_timeout(self) -> None:
        await build_request_async(options) # Made async
        gc.collect()

        tracemalloc.start(1000)

        snapshot_before = tracemalloc.take_snapshot()

        ITERATIONS = 10
        for _ in range(ITERATIONS):
            await build_request_async(options) # Made async

        gc.collect()
        snapshot_after = tracemalloc.take_snapshot()

        tracemalloc.stop()

        def add_leak(leaks: list[tracemalloc.StatisticDiff], diff: tracemalloc.StatisticDiff) -> None:
            if diff.count == 0:
                # Avoid false positives by considering only leaks (i.e. allocations that persist).
                return

            if diff.count % ITERATIONS != 0:
                # Avoid false positives by considering only leaks that appear per iteration.
                return

            for frame in diff.traceback:
                if any(
                    frame.filename.endswith(fragment)
                    for fragment in [
                        # to_raw_response_wrapper leaks through the @functools.wraps() decorator.
                        #
                        # removing the decorator fixes the leak for reasons we don't understand.
                        "openai/_legacy_response.py",
                        "openai/_response.py",
                        # pydantic.BaseModel.model_dump || pydantic.BaseModel.dict leak memory for some reason.
                        "openai/_compat.py",
                        # Standard library leaks we don't care about.
                        "/logging/__init__.py",
                        # Anyio path resolution can sometimes show up in CI, ignore it.
                        "/anyio/_core/_paths.py"
                    ]
                ):
                    return

            leaks.append(diff)

        leaks: list[tracemalloc.StatisticDiff] = []
        for diff in snapshot_after.compare_to(snapshot_before, "traceback"):
            add_leak(leaks, diff)
        if leaks:
            for leak in leaks:
                print("MEMORY LEAK:", leak)
                for frame in leak.traceback:
                    print(frame)
            raise AssertionError()

    @pytest.mark.asyncio
    def test_request_timeout(self) -> None:
        request = self.client._build_request(FinalRequestOptions(method="get", url="/foo"))
        timeout = httpx.Timeout(**request.extensions["timeout"])  # type: ignore
        assert timeout == DEFAULT_TIMEOUT

        request = self.client._build_request(
            FinalRequestOptions(method="get", url="/foo", timeout=httpx.Timeout(100.0))
        )
        timeout = httpx.Timeout(**request.extensions["timeout"])  # type: ignore
        assert timeout == httpx.Timeout(100.0)

    def test_client_timeout_option(self) -> None:
        client = OpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True, timeout=httpx.Timeout(0))

        request = client._build_request(FinalRequestOptions(method="get", url="/foo"))
        timeout = httpx.Timeout(**request.extensions["timeout"])  # type: ignore
        assert timeout == httpx.Timeout(0)

    def test_http_client_timeout_option(self) -> None:
        # custom timeout given to the httpx client should be used
        with httpx.Client(timeout=None) as http_client:
            client = OpenAI(
                base_url=base_url, api_key=api_key, _strict_response_validation=True, http_client=http_client
            )

            request = client._build_request(FinalRequestOptions(method="get", url="/foo"))
            timeout = httpx.Timeout(**request.extensions["timeout"])  # type: ignore
            assert timeout == httpx.Timeout(None)

        # no timeout given to the httpx client should not use the httpx default
        with httpx.Client() as http_client:
            client = OpenAI(
                base_url=base_url, api_key=api_key, _strict_response_validation=True, http_client=http_client
            )

            request = client._build_request(FinalRequestOptions(method="get", url="/foo"))
            timeout = httpx.Timeout(**request.extensions["timeout"])  # type: ignore
            assert timeout == DEFAULT_TIMEOUT

        # explicitly passing the default timeout currently results in it being ignored
        with httpx.Client(timeout=HTTPX_DEFAULT_TIMEOUT) as http_client:
            client = OpenAI(
                base_url=base_url, api_key=api_key, _strict_response_validation=True, http_client=http_client
            )

            request = client._build_request(FinalRequestOptions(method="get", url="/foo"))
            timeout = httpx.Timeout(**request.extensions["timeout"])  # type: ignore
            assert timeout == DEFAULT_TIMEOUT  # our default

    async def test_invalid_http_client(self) -> None:
        with pytest.raises(TypeError, match="Invalid `http_client` arg"):
            async with httpx.AsyncClient() as http_client:
                OpenAI(
                    base_url=base_url,
                    api_key=api_key,
                    _strict_response_validation=True,
                    http_client=cast(Any, http_client),
                )

    def test_default_headers_option(self) -> None:
        client = OpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, default_headers={"X-Foo": "bar"}
        )
        request = client._build_request(FinalRequestOptions(method="get", url="/foo"))
        assert request.headers.get("x-foo") == "bar"
        assert request.headers.get("x-stainless-lang") == "python"

        client2 = OpenAI(
            base_url=base_url,
            api_key=api_key,
            _strict_response_validation=True,
            default_headers={
                "X-Foo": "stainless",
                "X-Stainless-Lang": "my-overriding-header",
            },
        )
        request = client2._build_request(FinalRequestOptions(method="get", url="/foo"))
        assert request.headers.get("x-foo") == "stainless"
        assert request.headers.get("x-stainless-lang") == "my-overriding-header"

    def test_validate_headers(self) -> None:
        client = OpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        request = client._build_request(FinalRequestOptions(method="get", url="/foo"))
        assert request.headers.get("Authorization") == f"Bearer {api_key}"

        with pytest.raises(OpenAIError):
            with update_env(**{"OPENAI_API_KEY": Omit()}):
                client2 = OpenAI(base_url=base_url, api_key=None, _strict_response_validation=True)
            _ = client2

    def test_default_query_option(self) -> None:
        client = OpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, default_query={"query_param": "bar"}
        )
        request = client._build_request(FinalRequestOptions(method="get", url="/foo"))
        url = httpx.URL(request.url)
        assert dict(url.params) == {"query_param": "bar"}

        request = client._build_request(
            FinalRequestOptions(
                method="get",
                url="/foo",
                params={"foo": "baz", "query_param": "overridden"},
            )
        )
        url = httpx.URL(request.url)
        assert request_kwargs_override.get("params", {}).get("foo") == "baz"
            assert request_kwargs_override.get("params", {}).get("query_param") == "overridden"
        await client.close()

    @pytest.mark.asyncio
    async def test_request_extra_json(self) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        request_kwargs = await client._build_aiohttp_request_kwargs_with_files(
            FinalRequestOptions(
                method="post",
                url="/foo",
                json_data={"foo": "bar"},
                extra_json={"baz": False},
            ),
        )
        # For aiohttp, this will be under the 'json' key if no files, or 'data' (FormData) if files.
        # Assuming no files for this test.
        assert request_kwargs["json"] == {"foo": "bar", "baz": False}

        request_kwargs_no_main_json = await client._build_aiohttp_request_kwargs_with_files(
            FinalRequestOptions(
                method="post",
                url="/foo",
                extra_json={"baz": False},
            ),
        )
        assert request_kwargs_no_main_json["json"] == {"baz": False}

        # `extra_json` takes priority over `json_data` when keys clash
        request_kwargs_clash = await client._build_aiohttp_request_kwargs_with_files(
            FinalRequestOptions(
                method="post",
                url="/foo",
                json_data={"foo": "bar", "baz": True},
                extra_json={"baz": None},
            ),
        )
        data = json.loads(request.content.decode("utf-8"))
        assert data == {"foo": "bar", "baz": None}

    def test_request_extra_headers(self) -> None:
        request = self.client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(extra_headers={"X-Foo": "Foo"}),
            ),
        )
        assert request.headers.get("X-Foo") == "Foo"

        # `extra_headers` takes priority over `default_headers` when keys clash
        request = self.client.with_options(default_headers={"X-Bar": "true"})._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(
                    extra_headers={"X-Bar": "false"},
                ),
            ),
        )
        assert request.headers.get("X-Bar") == "false"

    def test_request_extra_query(self) -> None:
        request = self.client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(
                    extra_query={"my_query_param": "Foo"},
                ),
            ),
        )
        params = dict(request.url.params)
        assert params == {"my_query_param": "Foo"}

        # if both `query` and `extra_query` are given, they are merged
        request = self.client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(
                    query={"bar": "1"},
                    extra_query={"foo": "2"},
                ),
            ),
        )
        params = dict(request.url.params)
        assert params == {"bar": "1", "foo": "2"}

        # `extra_query` takes priority over `query` when keys clash
        request = self.client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(
                    query={"foo": "1"},
                    extra_query={"foo": "2"},
                ),
            ),
        )
        params = dict(request.url.params)
        assert params == {"foo": "2"}

    @pytest.mark.asyncio
    async def test_multipart_repeating_array(self) -> None: # Removed async_client fixture
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        
        # Data to be sent
        json_data = {"array": ["foo", "bar"]}
        file_content = b"hello world"
        files_data = [("foo.txt", ("upload", file_content, "application/octet-stream"))] # httpx format for files

        # Construct options as they would be before _build_aiohttp_request_kwargs_with_files
        # Note: headers for multipart are typically set by the client library (aiohttp here)
        options = FinalRequestOptions.construct( # type: ignore
            method="POST", # Multipart is typically POST
            url="/foo",
            # json_data and files are processed by _build_aiohttp_request_kwargs_with_files
            json_data=json_data,
            files=files_data, # type: ignore
        )

        request_kwargs = await client._build_aiohttp_request_kwargs_with_files(options)
        
        assert "data" in request_kwargs
        assert isinstance(request_kwargs["data"], aiohttp.FormData)
        
        form_data: aiohttp.FormData = request_kwargs["data"] # type: ignore
        
        # Inspecting FormData._fields (internal but useful for tests)
        # Expected structure: two fields for 'array[]', one for 'foo.txt'
        
        array_fields = []
        file_fields = []

        # _fields contains FormField instances or other data if not files.
        # aiohttp.FormData._fields is a list of tuples:
        # (headers, body_part_object) or for simple fields, it might be different.
        # Let's iterate and check based on field names.
        # Note: aiohttp may handle array parameters differently (e.g. multiple fields with same name)
        # The _serialize_multipartform method in BaseClient converts lists into duplicate key entries.
        
        # Due to the complexity of FormData's internal structure and how it's populated,
        # we'll check for the presence and basic properties of the expected parts.
        
        found_array_foo = False
        found_array_bar = False
        found_file = False

        for part in form_data._fields: # type: ignore
            # part is typically a tuple (name, value, filename, content_type) for files
            # or (name, value) for simple fields, but can be more complex.
            # We rely on how _async_populate_aiohttp_form_data and _serialize_multipartform structure it.
            
            part_name = part.name if hasattr(part, 'name') else None
            part_filename = part.filename if hasattr(part, 'filename') else None
            part_value = part.value if hasattr(part, 'value') else None # For simple fields
            part_content_type = part.content_type if hasattr(part, 'content_type') else None

            if part_name == "array[]":
                # FormData might store these as bytes after str conversion
                if part_value == b"foo":
                    found_array_foo = True
                elif part_value == b"bar":
                    found_array_bar = True
            elif part_name == "foo.txt": # Name given in files_data
                found_file = True
                assert part_filename == "upload" # Filename from tuple
                assert part_content_type == "application/octet-stream"
                # For files, part.value is the content.
                # If it's IOBase, read it. If bytes, compare directly.
                file_part_content = part_value
                if hasattr(part_value, 'read'): # If it's a stream-like object
                    if hasattr(part_value, 'seek'): part_value.seek(0)
                    file_part_content = part_value.read()
                assert file_part_content == file_content

        assert found_array_foo, "Multipart field 'array[]' with value 'foo' not found"
        assert found_array_bar, "Multipart field 'array[]' with value 'bar' not found"
        assert found_file, "Multipart file 'foo.txt' not found or misconfigured"
        
        await client.close()

    @pytest.mark.asyncio
    async def test_basic_union_response(self) -> None: # Removed respx_mock
        class Model1(BaseModel):
            name: str

        class Model2(BaseModel):
            foo: str

        respx_mock.get("/foo").mock(return_value=httpx.Response(200, json={"foo": "bar"}))

        response = self.client.get("/foo", cast_to=cast(Any, Union[Model1, Model2]))
        assert isinstance(response, Model2)
        assert response.foo == "bar"

    @pytest.mark.respx(base_url=base_url)
    def test_union_response_different_types(self, respx_mock: MockRouter) -> None:
        """Union of objects with the same field name using a different type"""

        class Model1(BaseModel):
            foo: int

        class Model2(BaseModel):
            foo: str

        respx_mock.get("/foo").mock(return_value=httpx.Response(200, json={"foo": "bar"}))

        response = self.client.get("/foo", cast_to=cast(Any, Union[Model1, Model2]))
        assert isinstance(response, Model2)
        assert response.foo == "bar"

        respx_mock.get("/foo").mock(return_value=httpx.Response(200, json={"foo": 1}))

        response = self.client.get("/foo", cast_to=cast(Any, Union[Model1, Model2]))
        assert isinstance(response, Model1)
        assert response.foo == 1

    @pytest.mark.respx(base_url=base_url)
    def test_non_application_json_content_type_for_json_data(self, respx_mock: MockRouter) -> None:
        """
        Response that sets Content-Type to something other than application/json but returns json data
        """

        class Model(BaseModel):
            foo: int

        respx_mock.get("/foo").mock(
            return_value=httpx.Response(
                200,
                content=json.dumps({"foo": 2}),
                headers={"Content-Type": "application/text"},
            )
        )

        response = self.client.get("/foo", cast_to=Model)
        assert isinstance(response, Model)
        assert response.foo == 2

    def test_base_url_setter(self) -> None:
        client = OpenAI(base_url="https://example.com/from_init", api_key=api_key, _strict_response_validation=True)
        assert client.base_url == "https://example.com/from_init/"

        client.base_url = "https://example.com/from_setter"  # type: ignore[assignment]

        assert client.base_url == "https://example.com/from_setter/"

    def test_base_url_env(self) -> None:
        with update_env(OPENAI_BASE_URL="http://localhost:5000/from/env"):
            client = OpenAI(api_key=api_key, _strict_response_validation=True)
            assert client.base_url == "http://localhost:5000/from/env/"

    @pytest.mark.parametrize(
        "client",
        [
            OpenAI(base_url="http://localhost:5000/custom/path/", api_key=api_key, _strict_response_validation=True),
            OpenAI(
                base_url="http://localhost:5000/custom/path/",
                api_key=api_key,
                _strict_response_validation=True,
                http_client=httpx.Client(),
            ),
        ],
        ids=["standard", "custom http client"],
    )
    def test_base_url_trailing_slash(self, client: OpenAI) -> None:
        request = client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                json_data={"foo": "bar"},
            ),
        )
        assert request.url == "http://localhost:5000/custom/path/foo"

    @pytest.mark.parametrize(
        "client",
        [
            OpenAI(base_url="http://localhost:5000/custom/path/", api_key=api_key, _strict_response_validation=True),
            OpenAI(
                base_url="http://localhost:5000/custom/path/",
                api_key=api_key,
                _strict_response_validation=True,
                http_client=httpx.Client(),
            ),
        ],
        ids=["standard", "custom http client"],
    )
    def test_base_url_no_trailing_slash(self, client: OpenAI) -> None:
        request = client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                json_data={"foo": "bar"},
            ),
        )
        assert request.url == "http://localhost:5000/custom/path/foo"

    @pytest.mark.parametrize(
        "client",
        [
            OpenAI(base_url="http://localhost:5000/custom/path/", api_key=api_key, _strict_response_validation=True),
            OpenAI(
                base_url="http://localhost:5000/custom/path/",
                api_key=api_key,
                _strict_response_validation=True,
                http_client=httpx.Client(),
            ),
        ],
        ids=["standard", "custom http client"],
    )
    def test_absolute_request_url(self, client: OpenAI) -> None:
        request = client._build_request(
            FinalRequestOptions(
                method="post",
                url="https://myapi.com/foo",
                json_data={"foo": "bar"},
            ),
        )
        assert request.url == "https://myapi.com/foo"

    def test_copied_client_does_not_close_http(self) -> None:
        client = OpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        assert not client.is_closed()

        copied = client.copy()
        assert copied is not client

        del copied

        assert not client.is_closed()

    def test_client_context_manager(self) -> None:
        client = OpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        with client as c2:
            assert c2 is client
            assert not c2.is_closed()
            assert not client.is_closed()
        assert client.is_closed()

    @pytest.mark.asyncio
    async def test_async_post_with_aiohttp_mock(self) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

        # Mock aiohttp.ClientSession and its request method
        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
        
        # Configure mock_response attributes
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        
        # Mock methods of ClientResponse
        mock_response.read = mock.AsyncMock(return_value=json.dumps({"foo": "bar"}).encode("utf-8"))
        mock_response.text = mock.AsyncMock(return_value=json.dumps({"foo": "bar"}))
        mock_response.json = mock.AsyncMock(return_value={"foo": "bar"})
        mock_response.release = mock.AsyncMock() # For stream release/close
        mock_response.closed = False

        # Mock response.content for streaming (if needed, basic for now)
        mock_stream_reader = mock.MagicMock(spec=aiohttp.StreamReader)
        async def mock_iter_any_empty(): # Simulating an empty stream or non-streaming case for .content
            if False: # Ensure it's an async generator
                yield b""
        mock_stream_reader.iter_any.return_value = mock_iter_any_empty()
        mock_response.content = mock_stream_reader

        # Setup the async context manager behavior for session.request()
        async_cm = mock.AsyncMock()
        async_cm.__aenter__.return_value = mock_response
        async_cm.__aexit__.return_value = None # Or mock.AsyncMock(return_value=None)
        mock_session.request.return_value = async_cm

        # Patch the client's internal _client to be our mock_session
        # This assumes AsyncAPIClient stores its session as self._client
        with mock.patch.object(client, "_client", mock_session):
            class Model(BaseModel):
                foo: str
            
            response_model = await client.post("/foo", body={"test": "data"}, cast_to=Model)
            assert isinstance(response_model, Model)
            assert response_model.foo == "bar"

            # Verify session.request was called correctly
            mock_session.request.assert_called_once()
            args, kwargs = mock_session.request.call_args
            assert args[0] == "POST" # method
            assert args[1] == f"{base_url}/foo" # url
            assert kwargs["json"] == {"test": "data"}
            assert "timeout" in kwargs # check if timeout was passed

        await client.close()


    @pytest.mark.respx(base_url=base_url)
    def test_client_response_validation_error(self, respx_mock: MockRouter) -> None:
        class Model(BaseModel):
            foo: str

        respx_mock.get("/foo").mock(return_value=httpx.Response(200, json={"foo": {"invalid": True}}))

        with pytest.raises(APIResponseValidationError) as exc:
            self.client.get("/foo", cast_to=Model)

        assert isinstance(exc.value.__cause__, ValidationError)

    def test_client_max_retries_validation(self) -> None:
        with pytest.raises(TypeError, match=r"max_retries cannot be None"):
            OpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True, max_retries=cast(Any, None))

    @pytest.mark.respx(base_url=base_url)
    def test_default_stream_cls(self, respx_mock: MockRouter) -> None:
        class Model(BaseModel):
            name: str

        respx_mock.post("/foo").mock(return_value=httpx.Response(200, json={"foo": "bar"}))

        stream = self.client.post("/foo", cast_to=Model, stream=True, stream_cls=Stream[Model])
        assert isinstance(stream, Stream)
        stream.response.close()

    @pytest.mark.respx(base_url=base_url)
    def test_received_text_for_expected_json(self, respx_mock: MockRouter) -> None:
        class Model(BaseModel):
            name: str

        respx_mock.get("/foo").mock(return_value=httpx.Response(200, text="my-custom-format"))

        strict_client = OpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

        with pytest.raises(APIResponseValidationError):
            strict_client.get("/foo", cast_to=Model)

        client = OpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=False)

        response = client.get("/foo", cast_to=Model)
        assert isinstance(response, str)  # type: ignore[unreachable]

    @pytest.mark.parametrize(
        "remaining_retries,retry_after,timeout",
        [
            [3, "20", 20],
            [3, "0", 0.5],
            [3, "-10", 0.5],
            [3, "60", 60],
            [3, "61", 0.5],
            [3, "Fri, 29 Sep 2023 16:26:57 GMT", 20],
            [3, "Fri, 29 Sep 2023 16:26:37 GMT", 0.5],
            [3, "Fri, 29 Sep 2023 16:26:27 GMT", 0.5],
            [3, "Fri, 29 Sep 2023 16:27:37 GMT", 60],
            [3, "Fri, 29 Sep 2023 16:27:38 GMT", 0.5],
            [3, "99999999999999999999999999999999999", 0.5],
            [3, "Zun, 29 Sep 2023 16:26:27 GMT", 0.5],
            [3, "", 0.5],
            [2, "", 0.5 * 2.0],
            [1, "", 0.5 * 4.0],
            [-1100, "", 8],  # test large number potentially overflowing
        ],
    )
    @mock.patch("time.time", mock.MagicMock(return_value=1696004797))
    def test_parse_retry_after_header(self, remaining_retries: int, retry_after: str, timeout: float) -> None:
        client = OpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

        headers = httpx.Headers({"retry-after": retry_after})
        options = FinalRequestOptions(method="get", url="/foo", max_retries=3)
        calculated = client._calculate_retry_timeout(remaining_retries, options, headers)
        assert calculated == pytest.approx(timeout, 0.5 * 0.875)  # pyright: ignore[reportUnknownMemberType]

    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.respx(base_url=base_url)
    def test_retrying_timeout_errors_doesnt_leak(self, respx_mock: MockRouter) -> None:
        respx_mock.post("/chat/completions").mock(side_effect=httpx.TimeoutException("Test timeout error"))

        with pytest.raises(APITimeoutError):
            self.client.post(
                "/chat/completions",
                body=cast(
                    object,
                    maybe_transform(
                        dict(
                            messages=[
                                {
                                    "role": "user",
                                    "content": "Say this is a test",
                                }
                            ],
                            model="gpt-4o",
                        ),
                        CompletionCreateParamsNonStreaming,
                    ),
                ),
                cast_to=httpx.Response,
                options={"headers": {RAW_RESPONSE_HEADER: "stream"}},
            )

        assert _get_open_connections(self.client) == 0

    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.respx(base_url=base_url)
    def test_retrying_status_errors_doesnt_leak(self, respx_mock: MockRouter) -> None:
        respx_mock.post("/chat/completions").mock(return_value=httpx.Response(500))

        with pytest.raises(APIStatusError):
            self.client.post(
                "/chat/completions",
                body=cast(
                    object,
                    maybe_transform(
                        dict(
                            messages=[
                                {
                                    "role": "user",
                                    "content": "Say this is a test",
                                }
                            ],
                            model="gpt-4o",
                        ),
                        CompletionCreateParamsNonStreaming,
                    ),
                ),
                cast_to=httpx.Response,
                options={"headers": {RAW_RESPONSE_HEADER: "stream"}},
            )

        assert _get_open_connections(self.client) == 0

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.respx(base_url=base_url)
    @pytest.mark.parametrize("failure_mode", ["status", "exception"])
    def test_retries_taken(
        self,
        client: OpenAI,
        failures_before_success: int,
        failure_mode: Literal["status", "exception"],
        respx_mock: MockRouter,
    ) -> None:
        client = client.with_options(max_retries=4)

        nb_retries = 0

        def retry_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal nb_retries
            if nb_retries < failures_before_success:
                nb_retries += 1
                if failure_mode == "exception":
                    raise RuntimeError("oops")
                return httpx.Response(500)
            return httpx.Response(200)

        respx_mock.post("/chat/completions").mock(side_effect=retry_handler)

        response = client.chat.completions.with_raw_response.create(
            messages=[
                {
                    "content": "string",
                    "role": "developer",
                }
            ],
            model="gpt-4o",
        )

        assert response.retries_taken == failures_before_success
        assert int(response.http_request.headers.get("x-stainless-retry-count")) == failures_before_success

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.respx(base_url=base_url)
    def test_omit_retry_count_header(
        self, client: OpenAI, failures_before_success: int, respx_mock: MockRouter
    ) -> None:
        client = client.with_options(max_retries=4)

        nb_retries = 0

        def retry_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal nb_retries
            if nb_retries < failures_before_success:
                nb_retries += 1
                return httpx.Response(500)
            return httpx.Response(200)

        respx_mock.post("/chat/completions").mock(side_effect=retry_handler)

        response = client.chat.completions.with_raw_response.create(
            messages=[
                {
                    "content": "string",
                    "role": "developer",
                }
            ],
            model="gpt-4o",
            extra_headers={"x-stainless-retry-count": Omit()},
        )

        assert len(response.http_request.headers.get_list("x-stainless-retry-count")) == 0

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.respx(base_url=base_url)
    def test_overwrite_retry_count_header(
        self, client: OpenAI, failures_before_success: int, respx_mock: MockRouter
    ) -> None:
        client = client.with_options(max_retries=4)

        nb_retries = 0

        def retry_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal nb_retries
            if nb_retries < failures_before_success:
                nb_retries += 1
                return httpx.Response(500)
            return httpx.Response(200)

        respx_mock.post("/chat/completions").mock(side_effect=retry_handler)

        response = client.chat.completions.with_raw_response.create(
            messages=[
                {
                    "content": "string",
                    "role": "developer",
                }
            ],
            model="gpt-4o",
            extra_headers={"x-stainless-retry-count": "42"},
        )

        assert response.http_request.headers.get("x-stainless-retry-count") == "42"

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.respx(base_url=base_url)
    def test_retries_taken_new_response_class(
        self, client: OpenAI, failures_before_success: int, respx_mock: MockRouter
    ) -> None:
        client = client.with_options(max_retries=4)

        nb_retries = 0

        def retry_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal nb_retries
            if nb_retries < failures_before_success:
                nb_retries += 1
                return httpx.Response(500)
            return httpx.Response(200)

        respx_mock.post("/chat/completions").mock(side_effect=retry_handler)

        with client.chat.completions.with_streaming_response.create(
            messages=[
                {
                    "content": "string",
                    "role": "developer",
                }
            ],
            model="gpt-4o",
        ) as response:
            assert response.retries_taken == failures_before_success
            assert int(response.http_request.headers.get("x-stainless-retry-count")) == failures_before_success


class TestAsyncOpenAI:
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

    @pytest.mark.asyncio
    async def test_raw_response(self) -> None: # Removed respx_mock
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        
        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_aio_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
        mock_aio_response.status = 200
        mock_aio_response.headers = {"Content-Type": "application/json"}
        # _convert_aiohttp_response_to_httpx_like will call .read()
        mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({"foo": "bar"}).encode("utf-8"))
        mock_aio_response.closed = False
        mock_aio_response.release = mock.AsyncMock()


        async_cm = mock.AsyncMock()
        async_cm.__aenter__.return_value = mock_aio_response
        async_cm.__aexit__.return_value = None
        mock_session.request.return_value = async_cm

        with mock.patch.object(client, "_client", mock_session):
            response = await client.post("/foo", cast_to=httpx.Response) # type: ignore
            assert response.status_code == 200
            assert isinstance(response, httpx.Response) # type: ignore
            assert response.json() == {"foo": "bar"}
        
        await client.close()

    @pytest.mark.asyncio
    async def test_raw_response_for_binary(self) -> None: # Removed respx_mock
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_aio_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
        mock_aio_response.status = 200
        mock_aio_response.headers = {"Content-Type": "application/binary"}
        # _convert_aiohttp_response_to_httpx_like will call .read()
        mock_aio_response.read = mock.AsyncMock(return_value='{"foo": "bar"}'.encode("utf-8"))
        mock_aiohttp_response.closed = False
        mock_aiohttp_response.release = mock.AsyncMock()


        async_cm = mock.AsyncMock()
        async_cm.__aenter__.return_value = mock_aio_response
        async_cm.__aexit__.return_value = None
        mock_session.request.return_value = async_cm

        with mock.patch.object(client, "_client", mock_session):
            response = await client.post("/foo", cast_to=httpx.Response) # type: ignore
            assert response.status_code == 200
            assert isinstance(response, httpx.Response) # type: ignore
            # httpx.Response.json() will try to decode content if content-type is binary
            assert response.json() == {"foo": "bar"}

        await client.close()

    def test_copy(self) -> None:
        copied = self.client.copy()
        assert id(copied) != id(self.client)

        copied = self.client.copy(api_key="another My API Key")
        assert copied.api_key == "another My API Key"
        assert self.client.api_key == "My API Key"

    def test_copy_default_options(self) -> None:
        # options that have a default are overridden correctly
        copied = self.client.copy(max_retries=7)
        assert copied.max_retries == 7
        assert self.client.max_retries == 2

        copied2 = copied.copy(max_retries=6)
        assert copied2.max_retries == 6
        assert copied.max_retries == 7

        # timeout
        assert isinstance(self.client.timeout, httpx.Timeout)
        copied = self.client.copy(timeout=None)
        assert copied.timeout is None
        assert isinstance(self.client.timeout, httpx.Timeout)

    def test_copy_default_headers(self) -> None:
        client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, default_headers={"X-Foo": "bar"}
        )
        assert client.default_headers["X-Foo"] == "bar"

        # does not override the already given value when not specified
        copied = client.copy()
        assert copied.default_headers["X-Foo"] == "bar"

        # merges already given headers
        copied = client.copy(default_headers={"X-Bar": "stainless"})
        assert copied.default_headers["X-Foo"] == "bar"
        assert copied.default_headers["X-Bar"] == "stainless"

        # uses new values for any already given headers
        copied = client.copy(default_headers={"X-Foo": "stainless"})
        assert copied.default_headers["X-Foo"] == "stainless"

        # set_default_headers

        # completely overrides already set values
        copied = client.copy(set_default_headers={})
        assert copied.default_headers.get("X-Foo") is None

        copied = client.copy(set_default_headers={"X-Bar": "Robert"})
        assert copied.default_headers["X-Bar"] == "Robert"

        with pytest.raises(
            ValueError,
            match="`default_headers` and `set_default_headers` arguments are mutually exclusive",
        ):
            client.copy(set_default_headers={}, default_headers={"X-Foo": "Bar"})

    def test_copy_default_query(self) -> None:
        client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, default_query={"foo": "bar"}
        )
        assert _get_params(client)["foo"] == "bar"

        # does not override the already given value when not specified
        copied = client.copy()
        assert _get_params(copied)["foo"] == "bar"

        # merges already given params
        copied = client.copy(default_query={"bar": "stainless"})
        params = _get_params(copied)
        assert params["foo"] == "bar"
        assert params["bar"] == "stainless"

        # uses new values for any already given headers
        copied = client.copy(default_query={"foo": "stainless"})
        assert _get_params(copied)["foo"] == "stainless"

        # set_default_query

        # completely overrides already set values
        copied = client.copy(set_default_query={})
        assert _get_params(copied) == {}

        copied = client.copy(set_default_query={"bar": "Robert"})
        assert _get_params(copied)["bar"] == "Robert"

        with pytest.raises(
            ValueError,
            # TODO: update
            match="`default_query` and `set_default_query` arguments are mutually exclusive",
        ):
            client.copy(set_default_query={}, default_query={"foo": "Bar"})

    def test_copy_signature(self) -> None:
        # ensure the same parameters that can be passed to the client are defined in the `.copy()` method
        init_signature = inspect.signature(
            # mypy doesn't like that we access the `__init__` property.
            self.client.__init__,  # type: ignore[misc]
        )
        copy_signature = inspect.signature(self.client.copy)
        exclude_params = {"transport", "proxies", "_strict_response_validation"}

        for name in init_signature.parameters.keys():
            if name in exclude_params:
                continue

            copy_param = copy_signature.parameters.get(name)
            assert copy_param is not None, f"copy() signature is missing the {name} param"

    def test_copy_build_request(self) -> None:
        options = FinalRequestOptions(method="get", url="/foo")

        def build_request(options: FinalRequestOptions) -> None:
            client = self.client.copy()
            client._build_request(options)

        # ensure that the machinery is warmed up before tracing starts.
        build_request(options)
        gc.collect()

        tracemalloc.start(1000)

        snapshot_before = tracemalloc.take_snapshot()

        ITERATIONS = 10
        for _ in range(ITERATIONS):
            build_request(options)

        gc.collect()
        snapshot_after = tracemalloc.take_snapshot()

        tracemalloc.stop()

        def add_leak(leaks: list[tracemalloc.StatisticDiff], diff: tracemalloc.StatisticDiff) -> None:
            if diff.count == 0:
                # Avoid false positives by considering only leaks (i.e. allocations that persist).
                return

            if diff.count % ITERATIONS != 0:
                # Avoid false positives by considering only leaks that appear per iteration.
                return

            for frame in diff.traceback:
                if any(
                    frame.filename.endswith(fragment)
                    for fragment in [
                        # to_raw_response_wrapper leaks through the @functools.wraps() decorator.
                        #
                        # removing the decorator fixes the leak for reasons we don't understand.
                        "openai/_legacy_response.py",
                        "openai/_response.py",
                        # pydantic.BaseModel.model_dump || pydantic.BaseModel.dict leak memory for some reason.
                        "openai/_compat.py",
                        # Standard library leaks we don't care about.
                        "/logging/__init__.py",
                    ]
                ):
                    return

            leaks.append(diff)

        leaks: list[tracemalloc.StatisticDiff] = []
        for diff in snapshot_after.compare_to(snapshot_before, "traceback"):
            add_leak(leaks, diff)
        if leaks:
            for leak in leaks:
                print("MEMORY LEAK:", leak)
                for frame in leak.traceback:
                    print(frame)
            raise AssertionError()

    @pytest.mark.asyncio
    async def test_request_timeout(self) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        
        # Default timeout
        options = FinalRequestOptions(method="get", url="/foo")
        request_kwargs = await client._build_aiohttp_request_kwargs_with_files(options)
        assert isinstance(request_kwargs.get("timeout"), aiohttp.ClientTimeout)
        # Assuming DEFAULT_TIMEOUT is an httpx.Timeout object.
        # The conversion logic in _build_aiohttp_request_kwargs_with_files maps this.
        # If DEFAULT_TIMEOUT.read is set, it should be sock_read. If only .timeout is set, it's total.
        expected_total = DEFAULT_TIMEOUT.timeout if isinstance(DEFAULT_TIMEOUT, HttpxTimeout) else DEFAULT_TIMEOUT.read_timeout # type: ignore
        if isinstance(DEFAULT_TIMEOUT, HttpxTimeout) and DEFAULT_TIMEOUT.read is not None: # type: ignore
             assert request_kwargs["timeout"].sock_read == DEFAULT_TIMEOUT.read # type: ignore
        elif expected_total:
             assert request_kwargs["timeout"].total == expected_total


        # Per-request timeout
        custom_timeout = HttpxTimeout(100.0) # type: ignore
        options_custom_timeout = FinalRequestOptions(method="get", url="/foo", timeout=custom_timeout)
        request_kwargs_custom = await client._build_aiohttp_request_kwargs_with_files(options_custom_timeout)
        assert isinstance(request_kwargs_custom.get("timeout"), aiohttp.ClientTimeout)
        assert request_kwargs_custom["timeout"].total == 100.0
        
        await client.close()

    @pytest.mark.asyncio
    async def test_client_timeout_option(self) -> None:
        # Test timeout set at client level
        client_custom_timeout = HttpxTimeout(0) # type: ignore
        client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, timeout=client_custom_timeout
        )
        options = FinalRequestOptions(method="get", url="/foo")
        request_kwargs = await client._build_aiohttp_request_kwargs_with_files(options)
        assert isinstance(request_kwargs.get("timeout"), aiohttp.ClientTimeout)
        assert request_kwargs["timeout"].total == 0 # httpx.Timeout(0) maps to total=0
        await client.close()

        # Test None timeout at client level
        client_none_timeout = AsyncOpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, timeout=None
        )
        options_none = FinalRequestOptions(method="get", url="/foo")
        request_kwargs_none = await client_none_timeout._build_aiohttp_request_kwargs_with_files(options_none)
         # If client.timeout is None, _build_aiohttp_request_kwargs_with_files might not add 'timeout'
         # to kwargs, letting aiohttp session default apply (which might be its own default or None).
        assert "timeout" not in request_kwargs_none or request_kwargs_none["timeout"] is None
        await client_none_timeout.close()


    @pytest.mark.asyncio
    async def test_http_client_timeout_option(self) -> None:
        # Custom timeout given to an externally provided aiohttp.ClientSession
        # The session's timeout is configured at its creation and used by aiohttp internally.
        # Our per-request timeout logic in _build_aiohttp_request_kwargs_with_files would override it.
        
        # Scenario 1: External client with its own timeout, no per-request timeout
        session_timeout = aiohttp.ClientTimeout(total=50.0)
        external_session = aiohttp.ClientSession(timeout=session_timeout)
        client_with_external_session = AsyncOpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, http_client=external_session
        )
        options1 = FinalRequestOptions(method="get", url="/foo")
        request_kwargs1 = await client_with_external_session._build_aiohttp_request_kwargs_with_files(options1)
        # Our client's default timeout (from DEFAULT_TIMEOUT) should be applied if no per-request timeout is set,
        # effectively overriding the external session's default for this specific call via request_kwargs.
        # This depends on client.timeout being DEFAULT_TIMEOUT when not specified.
        expected_total_default = DEFAULT_TIMEOUT.timeout if isinstance(DEFAULT_TIMEOUT, HttpxTimeout) else DEFAULT_TIMEOUT.read_timeout # type: ignore
        if isinstance(DEFAULT_TIMEOUT, HttpxTimeout) and DEFAULT_TIMEOUT.read is not None: # type: ignore
            assert request_kwargs1["timeout"].sock_read == DEFAULT_TIMEOUT.read # type: ignore
        elif expected_total_default:
            assert request_kwargs1["timeout"].total == expected_total_default
        await client_with_external_session.close() # Closes the session if owned, or just our wrapper logic.
                                           # Since session is external, it should not be closed by our client.
        await external_session.close() # User is responsible for closing external session.


        # Scenario 2: External client, but with a per-request timeout via options
        # Per-request timeout should always take precedence.
        external_session2 = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60.0))
        client_with_external_session2 = AsyncOpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, http_client=external_session2
        )
        per_request_httpx_timeout = HttpxTimeout(30.0) # type: ignore
        options2 = FinalRequestOptions(method="get", url="/foo", timeout=per_request_httpx_timeout)
        request_kwargs2 = await client_with_external_session2._build_aiohttp_request_kwargs_with_files(options2)
        assert isinstance(request_kwargs2.get("timeout"), aiohttp.ClientTimeout)
        assert request_kwargs2["timeout"].total == 30.0
        await client_with_external_session2.close()
        await external_session2.close()

    def test_invalid_http_client(self) -> None:
        with pytest.raises(TypeError, match="Invalid `http_client` arg"):
            # Pass a sync httpx.Client to AsyncOpenAI, which now expects aiohttp.ClientSession
            with httpx.Client() as http_client: # type: ignore
                AsyncOpenAI(
                    base_url=base_url,
                    api_key=api_key,
                    _strict_response_validation=True,
                    http_client=cast(Any, http_client),
                )

    @pytest.mark.asyncio
    async def test_default_headers_option(self) -> None:
        client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, default_headers={"X-Foo": "bar"}
        )
        request_kwargs = await client._build_aiohttp_request_kwargs_with_files(FinalRequestOptions(method="get", url="/foo"))
        assert request_kwargs["headers"]["x-foo"] == "bar" # headers are dict
        assert request_kwargs["headers"]["x-stainless-lang"] == "python"
        await client.close()

        client2 = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            _strict_response_validation=True,
            default_headers={
                "X-Foo": "stainless",
                "X-Stainless-Lang": "my-overriding-header",
            },
        )
        request_kwargs2 = await client2._build_aiohttp_request_kwargs_with_files(FinalRequestOptions(method="get", url="/foo"))
        assert request_kwargs2["headers"]["x-foo"] == "stainless"
        assert request_kwargs2["headers"]["x-stainless-lang"] == "my-overriding-header"
        await client2.close()

    @pytest.mark.asyncio
    async def test_validate_headers(self) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        request_kwargs = await client._build_aiohttp_request_kwargs_with_files(FinalRequestOptions(method="get", url="/foo"))
        assert request_kwargs["headers"]["Authorization"] == f"Bearer {api_key}"
        await client.close()

        with pytest.raises(OpenAIError):
            with update_env(**{"OPENAI_API_KEY": Omit()}): # type: ignore
                client2 = AsyncOpenAI(base_url=base_url, api_key=None, _strict_response_validation=True)
            # Instantiation itself might not call _build_request, but a subsequent call would fail.
            # For this test structure, we might need to explicitly make a call if validation is lazy.
            # However, api_key is checked in __init__.
            _ = client2 # This line might be enough if __init__ fails
        # No need to close client2 if it didn't initialize fully.

    @pytest.mark.asyncio
    async def test_default_query_option(self) -> None:
        client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, _strict_response_validation=True, default_query={"query_param": "bar"}
        )
        request_kwargs = await client._build_aiohttp_request_kwargs_with_files(FinalRequestOptions(method="get", url="/foo"))
        # Query params are stringified and under 'params' key for aiohttp
        # The _build_aiohttp_request_kwargs_with_files uses self.qs.stringify which returns a string
        # or compatible type for aiohttp's params. Let's assume it's dict after parsing that string.
        # For simplicity, we can check the source params before stringification if that's easier.
        # Or, parse the stringified params.
        # For now, let's assume the method correctly prepares params for aiohttp.
        # The method `_build_aiohttp_request_kwargs_with_files` stores them under 'params' as a string or dict.
        # If it's a string:
        if isinstance(request_kwargs.get("params"), str):
            assert "query_param=bar" in request_kwargs["params"]
        else: # If it's a dict
            assert request_kwargs.get("params", {}).get("query_param") == "bar"


        request_kwargs_override = await client._build_aiohttp_request_kwargs_with_files(
            FinalRequestOptions(
                method="get",
                url="/foo",
                params={"foo": "baz", "query_param": "overridden"},
            )
        )
        if isinstance(request_kwargs_override.get("params"), str):
            assert "foo=baz" in request_kwargs_override["params"]
            assert "query_param=overridden" in request_kwargs_override["params"]
        else:
            assert request_kwargs_override.get("params", {}).get("foo") == "baz"
            assert request_kwargs_override.get("params", {}).get("query_param") == "overridden"
        await client.close()

    @pytest.mark.asyncio
    async def test_request_extra_json(self) -> None:
        request = self.client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                json_data={"foo": "bar"},
                extra_json={"baz": False},
            ),
        )
        data = json.loads(request.content.decode("utf-8"))
        assert data == {"foo": "bar", "baz": False}

        request = self.client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                extra_json={"baz": False},
            ),
        )
        data = json.loads(request.content.decode("utf-8"))
        assert data == {"baz": False}

        # `extra_json` takes priority over `json_data` when keys clash
        request = self.client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                json_data={"foo": "bar", "baz": True},
                extra_json={"baz": None},
            ),
        )
        assert request_kwargs_clash["json"] == {"foo": "bar", "baz": None}
        await client.close()

    @pytest.mark.asyncio
    async def test_request_extra_headers(self) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        request_kwargs = await client._build_aiohttp_request_kwargs_with_files(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(extra_headers={"X-Foo": "Foo"}), # type: ignore
            ),
        )
        assert request_kwargs["headers"]["x-foo"] == "Foo"

        # `extra_headers` takes priority over `default_headers` when keys clash
        client_with_defaults = client.with_options(default_headers={"X-Bar": "true"})
        request_kwargs_override = await client_with_defaults._build_aiohttp_request_kwargs_with_files(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(extra_headers={"X-Bar": "false"}), # type: ignore
            ),
        )
        assert request_kwargs_override["headers"]["x-bar"] == "false"
        await client.close()
        await client_with_defaults.close()


    @pytest.mark.asyncio
    async def test_request_extra_query(self) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        request_kwargs = await client._build_aiohttp_request_kwargs_with_files(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(extra_query={"my_query_param": "Foo"}), # type: ignore
            ),
        )
        # Assuming params are stored as a dict; stringified by qs if needed by aiohttp later
        assert request_kwargs["params"]["my_query_param"] == "Foo" # type: ignore

        # if both `query` and `extra_query` are given, they are merged
        request_kwargs_merged = await client._build_aiohttp_request_kwargs_with_files(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(query={"bar": "1"}, extra_query={"foo": "2"}), # type: ignore
            ),
        )
        assert request_kwargs_merged["params"] == {"bar": "1", "foo": "2"} # type: ignore

        # `extra_query` takes priority over `query` when keys clash
        request_kwargs_clash = await client._build_aiohttp_request_kwargs_with_files(
            FinalRequestOptions(
                method="post",
                url="/foo",
                **make_request_options(query={"foo": "1"}, extra_query={"foo": "2"}), # type: ignore
            ),
        )
        assert request_kwargs_clash["params"]["foo"] == "2" # type: ignore
        await client.close()

    @pytest.mark.asyncio
    async def test_multipart_repeating_array(self) -> None: # Removed async_client
        request = async_client._build_request(
            FinalRequestOptions.construct(
                method="get",
                url="/foo",
                headers={"Content-Type": "multipart/form-data; boundary=6b7ba517decee4a450543ea6ae821c82"},
                json_data={"array": ["foo", "bar"]},
                files=[("foo.txt", b"hello world")],
            )
        )

        assert request.read().split(b"\r\n") == [
            b"--6b7ba517decee4a450543ea6ae821c82",
            b'Content-Disposition: form-data; name="array[]"',
            b"",
            b"foo",
            b"--6b7ba517decee4a450543ea6ae821c82",
            b'Content-Disposition: form-data; name="array[]"',
            b"",
            b"bar",
            b"--6b7ba517decee4a450543ea6ae821c82",
            b'Content-Disposition: form-data; name="foo.txt"; filename="upload"',
            b"Content-Type: application/octet-stream",
            b"",
            b"hello world",
            b"--6b7ba517decee4a450543ea6ae821c82--",
            b"",
        ]

    @pytest.mark.asyncio
    async def test_basic_union_response(self) -> None: # Removed respx_mock
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        class Model1(BaseModel):
            name: str
        class Model2(BaseModel):
            foo: str

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_aio_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
        mock_aio_response.status = 200
        mock_aio_response.headers = {"Content-Type": "application/json"}
        mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({"foo": "bar"}).encode("utf-8"))
        mock_aio_response.json = mock.AsyncMock(return_value={"foo": "bar"}) # For _convert_aiohttp_response_to_httpx_like
        mock_aio_response.closed = False
        mock_aio_response.release = mock.AsyncMock()
        
        async_cm = mock.AsyncMock()
        async_cm.__aenter__.return_value = mock_aio_response
        async_cm.__aexit__.return_value = None
        mock_session.request.return_value = async_cm

        with mock.patch.object(client, "_client", mock_session):
            response = await client.get("/foo", cast_to=cast(Any, Union[Model1, Model2]))
            assert isinstance(response, Model2)
            assert response.foo == "bar"
        await client.close()

    @pytest.mark.asyncio
    async def test_union_response_different_types(self) -> None: # Removed respx_mock
        """Union of objects with the same field name using a different type"""
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        class Model1(BaseModel):
            foo: int
        class Model2(BaseModel):
            foo: str

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_aio_response_str = mock.AsyncMock(spec=aiohttp.ClientResponse)
        mock_aio_response_str.status = 200
        mock_aio_response_str.headers = {"Content-Type": "application/json"}
        mock_aio_response_str.read = mock.AsyncMock(return_value=json.dumps({"foo": "bar"}).encode("utf-8"))
        mock_aio_response_str.json = mock.AsyncMock(return_value={"foo": "bar"})
        mock_aio_response_str.closed = False
        mock_aio_response_str.release = mock.AsyncMock()

        async_cm_str = mock.AsyncMock()
        async_cm_str.__aenter__.return_value = mock_aio_response_str
        async_cm_str.__aexit__.return_value = None
        
        mock_aio_response_int = mock.AsyncMock(spec=aiohttp.ClientResponse)
        mock_aio_response_int.status = 200
        mock_aio_response_int.headers = {"Content-Type": "application/json"}
        mock_aio_response_int.read = mock.AsyncMock(return_value=json.dumps({"foo": 1}).encode("utf-8"))
        mock_aio_response_int.json = mock.AsyncMock(return_value={"foo": 1})
        mock_aio_response_int.closed = False
        mock_aio_response_int.release = mock.AsyncMock()

        async_cm_int = mock.AsyncMock()
        async_cm_int.__aenter__.return_value = mock_aio_response_int
        async_cm_int.__aexit__.return_value = None

        with mock.patch.object(client, "_client", mock_session):
            mock_session.request.return_value = async_cm_str
            response_str = await client.get("/foo", cast_to=cast(Any, Union[Model1, Model2]))
            assert isinstance(response_str, Model2)
            assert response_str.foo == "bar"

            mock_session.request.return_value = async_cm_int
            response_int = await client.get("/foo", cast_to=cast(Any, Union[Model1, Model2]))
            assert isinstance(response_int, Model1)
            assert response_int.foo == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_non_application_json_content_type_for_json_data(self) -> None: # Removed respx_mock
        """
        Response that sets Content-Type to something other than application/json but returns json data
        """

        class Model(BaseModel):
            foo: int

        respx_mock.get("/foo").mock(
            return_value=httpx.Response(
                200,
                content=json.dumps({"foo": 2}),
                headers={"Content-Type": "application/text"},
            )
        )

        response = await self.client.get("/foo", cast_to=Model)
        assert isinstance(response, Model)
        assert response.foo == 2

    def test_base_url_setter(self) -> None:
        client = AsyncOpenAI(
            base_url="https://example.com/from_init", api_key=api_key, _strict_response_validation=True
        )
        assert client.base_url == "https://example.com/from_init/"

        client.base_url = "https://example.com/from_setter"  # type: ignore[assignment]

        assert client.base_url == "https://example.com/from_setter/"

    def test_base_url_env(self) -> None:
        with update_env(OPENAI_BASE_URL="http://localhost:5000/from/env"):
            client = AsyncOpenAI(api_key=api_key, _strict_response_validation=True)
            assert client.base_url == "http://localhost:5000/from/env/"

    @pytest.mark.parametrize(
        "client",
        [
            AsyncOpenAI(
                base_url="http://localhost:5000/custom/path/", api_key=api_key, _strict_response_validation=True
            ),
            AsyncOpenAI(
                base_url="http://localhost:5000/custom/path/",
                api_key=api_key,
                _strict_response_validation=True,
                http_client=httpx.AsyncClient(),
            ),
        ],
        ids=["standard", "custom http client"],
    )
    def test_base_url_trailing_slash(self, client: AsyncOpenAI) -> None:
        request = client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                json_data={"foo": "bar"},
            ),
        )
        assert request.url == "http://localhost:5000/custom/path/foo"

    @pytest.mark.parametrize(
        "client",
        [
            AsyncOpenAI(
                base_url="http://localhost:5000/custom/path/", api_key=api_key, _strict_response_validation=True
            ),
            AsyncOpenAI(
                base_url="http://localhost:5000/custom/path/",
                api_key=api_key,
                _strict_response_validation=True,
                http_client=httpx.AsyncClient(),
            ),
        ],
        ids=["standard", "custom http client"],
    )
    def test_base_url_no_trailing_slash(self, client: AsyncOpenAI) -> None:
        request = client._build_request(
            FinalRequestOptions(
                method="post",
                url="/foo",
                json_data={"foo": "bar"},
            ),
        )
        assert request.url == "http://localhost:5000/custom/path/foo"

    @pytest.mark.parametrize(
        "client",
        [
            AsyncOpenAI(
                base_url="http://localhost:5000/custom/path/", api_key=api_key, _strict_response_validation=True
            ),
            AsyncOpenAI(
                base_url="http://localhost:5000/custom/path/",
                api_key=api_key,
                _strict_response_validation=True,
                http_client=httpx.AsyncClient(),
            ),
        ],
        ids=["standard", "custom http client"],
    )
    def test_absolute_request_url(self, client: AsyncOpenAI) -> None:
        request = client._build_request(
            FinalRequestOptions(
                method="post",
                url="https://myapi.com/foo",
                json_data={"foo": "bar"},
            ),
        )
        assert request.url == "https://myapi.com/foo"

    async def test_copied_client_does_not_close_http(self) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        assert not client.is_closed()

        copied = client.copy()
        assert copied is not client

        del copied

        await asyncio.sleep(0.2)
        assert not client.is_closed()

    async def test_client_context_manager(self) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        async with client as c2:
            assert c2 is client
            assert not c2.is_closed()
            assert not client.is_closed()
        assert client.is_closed()

    @pytest.mark.respx(base_url=base_url)
    @pytest.mark.asyncio
    async def test_client_response_validation_error(self, respx_mock: MockRouter) -> None:
        class Model(BaseModel):
            foo: str

        respx_mock.get("/foo").mock(return_value=httpx.Response(200, json={"foo": {"invalid": True}}))

        with pytest.raises(APIResponseValidationError) as exc:
            await self.client.get("/foo", cast_to=Model)

        assert isinstance(exc.value.__cause__, ValidationError)

    async def test_client_max_retries_validation(self) -> None:
        with pytest.raises(TypeError, match=r"max_retries cannot be None"):
            AsyncOpenAI(
                base_url=base_url, api_key=api_key, _strict_response_validation=True, max_retries=cast(Any, None)
            )

    @pytest.mark.asyncio
    async def test_default_stream_cls(self) -> None: # Removed respx_mock
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)
        class Model(BaseModel):
            name: str

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_aio_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
        mock_aio_response.status = 200
        mock_aio_response.headers = {"Content-Type": "text/event-stream"} # SSE content type
        
        mock_stream_reader = mock.MagicMock(spec=aiohttp.StreamReader)
        sse_event_data = json.dumps({"name": " streamed_response"})
        sse_done_event = b"data: [DONE]\n\n"
        async def sse_data_stream():
            yield f"data: {sse_event_data}\n\n".encode("utf-8")
            yield sse_done_event
        mock_stream_reader.iter_any.return_value = sse_data_stream()
        mock_aio_response.content = mock_stream_reader
        mock_aio_response.closed = False
        mock_aio_response.release = mock.AsyncMock()
         # Mock request_info for APIError compatibility within AsyncStream
        mock_request_info = mock.Mock(spec=aiohttp.helpers.RequestInfo) # type: ignore
        mock_request_info.url = client.base_url.join("/foo") # type: ignore
        mock_request_info.method = "POST"
        mock_request_info.headers = {} # type: ignore
        mock_aio_response.request_info = mock_request_info


        async_cm = mock.AsyncMock()
        async_cm.__aenter__.return_value = mock_aio_response
        async_cm.__aexit__.return_value = None
        mock_session.request.return_value = async_cm

        with mock.patch.object(client, "_client", mock_session):
            stream = await client.post("/foo", body={}, cast_to=Model, stream=True, stream_cls=AsyncStream[Model])
            assert isinstance(stream, AsyncStream)
            
            # Consume the stream to ensure proper handling
            results = []
            async for item in stream:
                results.append(item)
            
            assert len(results) == 1
            assert isinstance(results[0], Model)
            assert results[0].name == " streamed_response"
            
            # AsyncStream.close() calls response.release() for aiohttp
            await stream.close()
            mock_aio_response.release.assert_called_once()

        await client.close()

    @pytest.mark.asyncio
    async def test_received_text_for_expected_json(self) -> None: # Removed respx_mock
        class Model(BaseModel):
            name: str

        respx_mock.get("/foo").mock(return_value=httpx.Response(200, text="my-custom-format"))

        strict_client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

        with pytest.raises(APIResponseValidationError):
            await strict_client.get("/foo", cast_to=Model)

        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=False)

        response = await client.get("/foo", cast_to=Model)
        assert isinstance(response, str)  # type: ignore[unreachable]

    @pytest.mark.parametrize(
        "remaining_retries,retry_after,timeout",
        [
            [3, "20", 20],
            [3, "0", 0.5],
            [3, "-10", 0.5],
            [3, "60", 60],
            [3, "61", 0.5],
            [3, "Fri, 29 Sep 2023 16:26:57 GMT", 20],
            [3, "Fri, 29 Sep 2023 16:26:37 GMT", 0.5],
            [3, "Fri, 29 Sep 2023 16:26:27 GMT", 0.5],
            [3, "Fri, 29 Sep 2023 16:27:37 GMT", 60],
            [3, "Fri, 29 Sep 2023 16:27:38 GMT", 0.5],
            [3, "99999999999999999999999999999999999", 0.5],
            [3, "Zun, 29 Sep 2023 16:26:27 GMT", 0.5],
            [3, "", 0.5],
            [2, "", 0.5 * 2.0],
            [1, "", 0.5 * 4.0],
            [-1100, "", 8],  # test large number potentially overflowing
        ],
    )
    @mock.patch("time.time", mock.MagicMock(return_value=1696004797))
    @pytest.mark.asyncio
    async def test_parse_retry_after_header(self, remaining_retries: int, retry_after: str, timeout: float) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

        headers = httpx.Headers({"retry-after": retry_after})
        options = FinalRequestOptions(method="get", url="/foo", max_retries=3)
        calculated = client._calculate_retry_timeout(remaining_retries, options, headers)
        assert calculated == pytest.approx(timeout, 0.5 * 0.875)  # pyright: ignore[reportUnknownMemberType]

    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.asyncio
    async def test_retrying_timeout_errors_doesnt_leak(self) -> None: # Removed respx_mock
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        # Simulate a timeout error during the request call
        mock_session.request.side_effect = asyncio.TimeoutError("Test timeout error")

        with mock.patch.object(client, "_client", mock_session):
            with pytest.raises(APITimeoutError):
                await client.post(
                    "/chat/completions",
                    body=cast(
                        object,
                        maybe_transform(
                            dict(
                                messages=[
                                    {
                                        "role": "user",
                                        "content": "Say this is a test",
                                    }
                                ],
                                model="gpt-4o",
                            ),
                            CompletionCreateParamsNonStreaming,
                        ),
                    ),
                    cast_to=httpx.Response, # Cast_to is less relevant here as error is raised before response processing
                    options={"headers": {RAW_RESPONSE_HEADER: "stream"}}, # This option might not be fully relevant for timeout before response
                )
        
        # For aiohttp, direct open connection count via httpx pool is not applicable.
        # We ensure the client can be closed, implying session cleanup.
        await client.close()
        assert client.is_closed()


    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.asyncio
    async def test_retrying_status_errors_doesnt_leak(self) -> None: # Removed respx_mock
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True)

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_aio_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
        mock_aio_response.status = 500
        mock_aio_response.headers = {"Content-Type": "application/json"}
        mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({"error": "server error"}).encode("utf-8"))
        mock_aio_response.closed = False
        # Ensure release is called during error handling or response conversion
        mock_aio_response.release = mock.AsyncMock()
        
        # For _build_temporary_httpx_response, which might be called by _make_status_error_from_response
        # via _convert_aiohttp_response_to_httpx_like or directly if error occurs in retry loop.
        # It needs request_info for constructing the pseudo httpx.Request.
        mock_request_info = mock.Mock(spec=aiohttp.helpers.RequestInfo) # type: ignore
        mock_request_info.url = client.base_url.join("/chat/completions") # type: ignore
        mock_request_info.method = "POST"
        mock_request_info.headers = {"Content-Type": "application/json"}
        mock_aio_response.request_info = mock_request_info


        async_cm = mock.AsyncMock()
        async_cm.__aenter__.return_value = mock_aio_response
        async_cm.__aexit__.return_value = None
        mock_session.request.return_value = async_cm
        
        with mock.patch.object(client, "_client", mock_session):
            with pytest.raises(APIStatusError): # Or more specific InternalServerError
                await client.post(
                    "/chat/completions",
                    body=cast(
                        object,
                        maybe_transform(
                            dict(
                                messages=[
                                    {
                                        "role": "user",
                                        "content": "Say this is a test",
                                    }
                                ],
                                model="gpt-4o",
                            ),
                            CompletionCreateParamsNonStreaming,
                        ),
                    ),
                    cast_to=httpx.Response, # Cast_to might not be reached if error is raised early
                    options={"headers": {RAW_RESPONSE_HEADER: "stream"}},
                )
        
        # Verify that release was called on the mock response,
        # which indicates cleanup by _convert_aiohttp_response_to_httpx_like
        # or _process_response.
        if not client.with_streaming_response: # type: ignore
            mock_aio_response.release.assert_called()

        await client.close()
        assert client.is_closed()

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_mode", ["status", "exception"])
    async def test_retries_taken(
        self,
        # async_client: AsyncOpenAI, # Replaced by direct instantiation
        failures_before_success: int,
        failure_mode: Literal["status", "exception"],
        # respx_mock: MockRouter, # Removed
    ) -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True).with_options(max_retries=4)

        nb_calls = 0
        actual_request_headers: list[Any] = []

        async def mock_request_side_effect(*args: Any, **kwargs: Any) -> mock.AsyncMock: # Returns the AsyncContextManager
            nonlocal nb_calls, actual_request_headers
            
            # Store actual headers from the call to aiohttp
            actual_request_headers.append(kwargs.get("headers"))

            mock_aio_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
            mock_aio_response.closed = False
            mock_aio_response.release = mock.AsyncMock()
            
            # Mock request_info for error handling path if needed
            mock_request_info = mock.Mock(spec=aiohttp.helpers.RequestInfo) # type: ignore
            mock_request_info.url = client.base_url.join(args[1] if len(args) > 1 else kwargs.get("url", "")) # type: ignore
            mock_request_info.method = args[0] if len(args) > 0 else kwargs.get("method", "GET")
            mock_request_info.headers = kwargs.get("headers", {})
            mock_aio_response.request_info = mock_request_info

            if nb_calls < failures_before_success:
                nb_calls += 1
                if failure_mode == "exception":
                    # Simulate a retryable connection error for aiohttp
                    raise aiohttp.ClientConnectorError(connection_key=mock.Mock(), os_error=OSError("oops"))
                
                mock_aio_response.status = 500
                mock_aio_response.headers = {"Content-Type": "application/json", "retry-after": "0.1"} 
                # _build_temporary_httpx_response reads the body
                mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({"error": "server error"}).encode("utf-8"))
            else:
                nb_calls += 1
                mock_aio_response.status = 200
                mock_aio_response.headers = {"Content-Type": "application/json"}
                # Successful response body for chat completions
                mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1677652288,
                    "model": "gpt-4o",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello!"},
                        "finish_reason": "stop"
                    }],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21}
                }).encode("utf-8"))
            
            # Return an async context manager that yields the mock_aio_response
            async_cm = mock.AsyncMock()
            async_cm.__aenter__.return_value = mock_aio_response
            async_cm.__aexit__.return_value = None
            return async_cm

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_session.request.side_effect = mock_request_side_effect
        
        with mock.patch.object(client, "_client", mock_session):
            response = await client.chat.completions.with_raw_response.create(
                messages=[{"role": "user", "content": "Say this is a test"}],
                model="gpt-4o",
            )

        assert response.retries_taken == failures_before_success
        # Check the retry count header for each attempt
        for i in range(failures_before_success + 1):
            # actual_request_headers might be CIMultiDictProxy if directly from aiohttp, or dict if from our builder
            # self._build_headers (in BaseClient) creates httpx.Headers, then dict() is called.
            # So it should be a dict here.
            retry_count_header = actual_request_headers[i].get("x-stainless-retry-count")
            assert retry_count_header is not None
            assert int(retry_count_header) == i
        
        await client.close()

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.respx(base_url=base_url)
    @pytest.mark.asyncio
    async def test_omit_retry_count_header(
        self, async_client: AsyncOpenAI, failures_before_success: int, respx_mock: MockRouter
    ) -> None:
        client = async_client.with_options(max_retries=4)

        nb_retries = 0

        def retry_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal nb_retries
            if nb_retries < failures_before_success:
                nb_retries += 1
                return httpx.Response(500)
            return httpx.Response(200)

        respx_mock.post("/chat/completions").mock(side_effect=retry_handler)

        response = await client.chat.completions.with_raw_response.create(
                "/chat/completions",
                body=cast(
                    object,
                    maybe_transform(
                        dict(
                            messages=[
                                {
                                    "role": "user",
                                    "content": "Say this is a test",
                                }
                            ],
                            model="gpt-4o",
                        ),
                        CompletionCreateParamsNonStreaming,
                    ),
                ),
                cast_to=httpx.Response,
                options={"headers": {RAW_RESPONSE_HEADER: "stream"}},
            )

        assert _get_open_connections(self.client) == 0

    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.respx(base_url=base_url)
    async def test_retrying_status_errors_doesnt_leak(self, respx_mock: MockRouter) -> None:
        respx_mock.post("/chat/completions").mock(return_value=httpx.Response(500))

        with pytest.raises(APIStatusError):
            await self.client.post(
                "/chat/completions",
                body=cast(
                    object,
                    maybe_transform(
                        dict(
                            messages=[
                                {
                                    "role": "user",
                                    "content": "Say this is a test",
                                }
                            ],
                            model="gpt-4o",
                        ),
                        CompletionCreateParamsNonStreaming,
                    ),
                ),
                cast_to=httpx.Response,
                options={"headers": {RAW_RESPONSE_HEADER: "stream"}},
            )

        assert _get_open_connections(self.client) == 0

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.respx(base_url=base_url)
    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_mode", ["status", "exception"])
    async def test_retries_taken(
        self,
        async_client: AsyncOpenAI,
        failures_before_success: int,
        failure_mode: Literal["status", "exception"],
        respx_mock: MockRouter,
    ) -> None:
        client = async_client.with_options(max_retries=4)

        nb_retries = 0

        def retry_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal nb_retries
            if nb_retries < failures_before_success:
                nb_retries += 1
                if failure_mode == "exception":
                    raise RuntimeError("oops")
                return httpx.Response(500)
            return httpx.Response(200)

        respx_mock.post("/chat/completions").mock(side_effect=retry_handler)

        response = await client.chat.completions.with_raw_response.create(
            messages=[
                {
                    "content": "string",
                    "role": "developer",
                }
            ],
            model="gpt-4o",
        )

        assert response.retries_taken == failures_before_success
        assert int(response.http_request.headers.get("x-stainless-retry-count")) == failures_before_success

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.asyncio
    async def test_omit_retry_count_header(
        self, failures_before_success: int
    ) -> None: # Removed async_client, respx_mock
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True).with_options(max_retries=4)
        
        nb_calls = 0
        actual_request_headers: list[Any] = []

        async def mock_request_side_effect(*args: Any, **kwargs: Any) -> mock.AsyncMock:
            nonlocal nb_calls, actual_request_headers
            actual_request_headers.append(kwargs.get("headers"))
            
            mock_aio_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
            mock_aio_response.closed = False
            mock_aio_response.release = mock.AsyncMock()
            mock_request_info = mock.Mock(spec=aiohttp.helpers.RequestInfo) # type: ignore
            mock_request_info.url = client.base_url.join(args[1] if len(args) > 1 else kwargs.get("url", "")) # type: ignore
            mock_request_info.method = args[0] if len(args) > 0 else kwargs.get("method", "GET")
            mock_request_info.headers = kwargs.get("headers", {})
            mock_aio_response.request_info = mock_request_info

            if nb_calls < failures_before_success:
                nb_calls += 1
                mock_aio_response.status = 500
                mock_aio_response.headers = {"Content-Type": "application/json", "retry-after": "0.1"}
                mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({"error": "server error"}).encode("utf-8"))
            else:
                nb_calls += 1
                mock_aio_response.status = 200
                mock_aio_response.headers = {"Content-Type": "application/json"}
                mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({
                    "id": "chatcmpl-test", "object": "chat.completion", "created": 123, "model": "gpt-4o",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Omitted headers!"}, "finish_reason": "stop"}]
                }).encode("utf-8"))

            async_cm = mock.AsyncMock()
            async_cm.__aenter__.return_value = mock_aio_response
            async_cm.__aexit__.return_value = None
            return async_cm

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_session.request.side_effect = mock_request_side_effect

        with mock.patch.object(client, "_client", mock_session):
            response = await client.chat.completions.with_raw_response.create(
                messages=[{"role": "user", "content": "Say this is a test"}],
                model="gpt-4o",
                extra_headers={"x-stainless-retry-count": Omit()},
            )

        assert response.retries_taken == failures_before_success
        for headers in actual_request_headers:
            assert "x-stainless-retry-count" not in headers
        
        await client.close()

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.asyncio
    async def test_overwrite_retry_count_header(
        self, failures_before_success: int
    ) -> None: # Removed async_client, respx_mock
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True).with_options(max_retries=4)

        nb_calls = 0
        actual_request_headers: list[Any] = []

        async def mock_request_side_effect(*args: Any, **kwargs: Any) -> mock.AsyncMock:
            nonlocal nb_calls, actual_request_headers
            actual_request_headers.append(kwargs.get("headers"))

            mock_aio_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
            mock_aio_response.closed = False
            mock_aio_response.release = mock.AsyncMock()
            mock_request_info = mock.Mock(spec=aiohttp.helpers.RequestInfo) # type: ignore
            mock_request_info.url = client.base_url.join(args[1] if len(args) > 1 else kwargs.get("url", "")) # type: ignore
            mock_request_info.method = args[0] if len(args) > 0 else kwargs.get("method", "GET")
            mock_request_info.headers = kwargs.get("headers", {})
            mock_aio_response.request_info = mock_request_info

            if nb_calls < failures_before_success:
                nb_calls += 1
                mock_aio_response.status = 500
                mock_aio_response.headers = {"Content-Type": "application/json", "retry-after": "0.1"}
                mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({"error": "server error"}).encode("utf-8"))
            else:
                nb_calls += 1
                mock_aio_response.status = 200
                mock_aio_response.headers = {"Content-Type": "application/json"}
                mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({
                     "id": "chatcmpl-test", "object": "chat.completion", "created": 123, "model": "gpt-4o",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Overwritten headers!"}, "finish_reason": "stop"}]
                }).encode("utf-8"))
            
            async_cm = mock.AsyncMock()
            async_cm.__aenter__.return_value = mock_aio_response
            async_cm.__aexit__.return_value = None
            return async_cm

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_session.request.side_effect = mock_request_side_effect

        with mock.patch.object(client, "_client", mock_session):
            response = await client.chat.completions.with_raw_response.create(
            messages=[
                {
                    "content": "string",
                    "role": "developer",
                }
            ],
            model="gpt-4o",
            extra_headers={"x-stainless-retry-count": "42"},
        )

        assert response.http_request.headers.get("x-stainless-retry-count") == "42"

    @pytest.mark.parametrize("failures_before_success", [0, 2, 4])
    @mock.patch("openai._base_client.BaseClient._calculate_retry_timeout", _low_retry_timeout)
    @pytest.mark.asyncio
    async def test_retries_taken_new_response_class(
        self, failures_before_success: int
    ) -> None: # Removed async_client, respx_mock
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, _strict_response_validation=True).with_options(max_retries=4)

        nb_calls = 0
        actual_request_headers: list[Any] = []

        # Minimal valid SSE data for chat completion stream
        sse_event_data = json.dumps({
            "id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 123, "model": "gpt-4o",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello"}, "finish_reason": None}]
        })
        sse_done_event = b"data: [DONE]\n\n"

        async def mock_request_side_effect(*args: Any, **kwargs: Any) -> mock.AsyncMock:
            nonlocal nb_calls, actual_request_headers
            actual_request_headers.append(kwargs.get("headers"))

            mock_aio_response = mock.AsyncMock(spec=aiohttp.ClientResponse)
            mock_aio_response.closed = False
            mock_aio_response.release = mock.AsyncMock()
            mock_request_info = mock.Mock(spec=aiohttp.helpers.RequestInfo) # type: ignore
            mock_request_info.url = client.base_url.join(args[1] if len(args) > 1 else kwargs.get("url", "")) # type: ignore
            mock_request_info.method = args[0] if len(args) > 0 else kwargs.get("method", "GET")
            mock_request_info.headers = kwargs.get("headers", {})
            mock_aio_response.request_info = mock_request_info
            
            mock_stream_reader = mock.MagicMock(spec=aiohttp.StreamReader)

            if nb_calls < failures_before_success:
                nb_calls += 1
                mock_aio_response.status = 500
                mock_aio_response.headers = {"Content-Type": "application/json", "retry-after": "0.1"}
                mock_aio_response.read = mock.AsyncMock(return_value=json.dumps({"error": "server error"}).encode("utf-8"))
                # For streaming errors, content might be empty or contain error details if not JSON
                async def empty_stream():
                    if False: yield b"" # Make it an async generator
                mock_stream_reader.iter_any.return_value = empty_stream()
            else:
                nb_calls += 1
                mock_aio_response.status = 200
                mock_aio_response.headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
                
                async def sse_data_stream():
                    yield f"data: {sse_event_data}\n\n".encode("utf-8")
                    yield sse_done_event
                mock_stream_reader.iter_any.return_value = sse_data_stream()
            
            mock_aio_response.content = mock_stream_reader
            
            async_cm = mock.AsyncMock()
            async_cm.__aenter__.return_value = mock_aio_response
            async_cm.__aexit__.return_value = None
            return async_cm

        mock_session = mock.AsyncMock(spec=aiohttp.ClientSession)
        mock_session.request.side_effect = mock_request_side_effect

        with mock.patch.object(client, "_client", mock_session):
            async with client.chat.completions.with_streaming_response.create(
                messages=[{"role": "user", "content": "Say this is a test"}],
                model="gpt-4o",
            ) as response:
                assert response.retries_taken == failures_before_success
                # The raw_response_header is added by with_streaming_response wrappers.
                # The pseudo_request_for_error_reporting in AsyncAPIClient.request gets its headers from request_kwargs,
                # which are built by _build_aiohttp_request_kwargs_with_files.
                # _build_headers is called there.
                # So, actual_request_headers should contain the x-stainless-retry-count.
                for i in range(failures_before_success + 1):
                    retry_count_header = actual_request_headers[i].get("x-stainless-retry-count")
                    assert retry_count_header is not None, f"x-stainless-retry-count missing in attempt {i}"
                    assert int(retry_count_header) == i
                
                # Consume the stream to ensure no errors during processing
                stream_yielded = False
                async for _ in response.parse(): # Assuming parse() is the method to get AsyncStream
                    stream_yielded = True
                if failures_before_success < nb_calls : # only expect yield if successful
                     assert stream_yielded is True
        
        await client.close()

    def test_get_platform(self) -> None:
        # A previous implementation of asyncify could leave threads unterminated when
        # used with nest_asyncio.
        #
        # Since nest_asyncio.apply() is global and cannot be un-applied, this
        # test is run in a separate process to avoid affecting other tests.
        test_code = dedent("""
        import asyncio
        import nest_asyncio
        import threading

        from openai._utils import asyncify
        from openai._base_client import get_platform

        async def test_main() -> None:
            result = await asyncify(get_platform)()
            print(result)
            for thread in threading.enumerate():
                print(thread.name)

        nest_asyncio.apply()
        asyncio.run(test_main())
        """)
        with subprocess.Popen(
            [sys.executable, "-c", test_code],
            text=True,
        ) as process:
            timeout = 10  # seconds

            start_time = time.monotonic()
            while True:
                return_code = process.poll()
                if return_code is not None:
                    if return_code != 0:
                        raise AssertionError("calling get_platform using asyncify resulted in a non-zero exit code")

                    # success
                    break

                if time.monotonic() - start_time > timeout:
                    process.kill()
                    raise AssertionError("calling get_platform using asyncify resulted in a hung process")

                time.sleep(0.1)
