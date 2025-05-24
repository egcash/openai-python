from __future__ import annotations

import sys
import json
import time
import uuid
import email
import asyncio
import inspect
import logging
import platform
import email.utils
import os # For os.PathLike and os.path.basename
from types import TracebackType
from random import random
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Type,
    Union,
    Generic,
    Mapping,
    TypeVar,
    Iterable,
    Iterator,
    Optional,
    Generator,
    AsyncIterator,
    cast,
    overload,
    IO,
    List,
    Sequence,
)
from typing_extensions import Literal, override, get_origin

import anyio
import httpx # type: ignore
import distro # type: ignore
import aiohttp # type: ignore
import pydantic # type: ignore
from httpx import URL # type: ignore
from pydantic import PrivateAttr # type: ignore

from . import _exceptions
from ._qs import Querystring
from ._files import FileTypes, is_file_content, is_mapping_t, is_sequence_t, is_tuple_t, FileContent
from ._types import (
    NOT_GIVEN,
    Body,
    Omit,
    Query,
    Headers,
    Timeout, 
    NotGiven,
    ResponseT,
    AnyMapping,
    PostParser,
    RequestFiles,
    HttpxSendArgs, 
    RequestOptions,
    ModelBuilderProtocol,
)
from ._utils import SensitiveHeadersFilter, is_dict, is_list, asyncify, is_given, lru_cache, is_mapping
from ._compat import PYDANTIC_V2, model_copy, model_dump
from ._models import GenericModel, FinalRequestOptions, validate_type, construct_type
from ._response import (
    APIResponse,
    BaseAPIResponse,
    AsyncAPIResponse,
    extract_response_type,
)
from ._constants import (
    DEFAULT_TIMEOUT,
    MAX_RETRY_DELAY,
    DEFAULT_MAX_RETRIES,
    INITIAL_RETRY_DELAY,
    RAW_RESPONSE_HEADER,
    OVERRIDE_CAST_TO_HEADER,
    DEFAULT_CONNECTION_LIMITS,
)
from ._streaming import Stream, SSEDecoder, AsyncStream, SSEBytesDecoder
from ._exceptions import (
    APIStatusError,
    APITimeoutError,
    APIConnectionError,
    APIResponseValidationError,
)
from ._legacy_response import LegacyAPIResponse


log: logging.Logger = logging.getLogger(__name__)
log.addFilter(SensitiveHeadersFilter())

SyncPageT = TypeVar("SyncPageT", bound="BaseSyncPage[Any]")
AsyncPageT = TypeVar("AsyncPageT", bound="BaseAsyncPage[Any]")


_T = TypeVar("_T")
_T_co = TypeVar("_T_co", covariant=True)

_StreamT = TypeVar("_StreamT", bound=Stream[Any])
_AsyncStreamT = TypeVar("_AsyncStreamT", bound=AsyncStream[Any])

if TYPE_CHECKING:
    from httpx._config import ( # type: ignore
        DEFAULT_TIMEOUT_CONFIG as HTTPX_DEFAULT_TIMEOUT_CONFIG,
    )
    HttpxTimeout = httpx.Timeout # type: ignore
else:
    try:
        from httpx._config import DEFAULT_TIMEOUT_CONFIG as HTTPX_DEFAULT_TIMEOUT_CONFIG # type: ignore
        HttpxTimeout = httpx.Timeout # type: ignore
    except ImportError:
        class HttpxTimeout: # type: ignore
            def __init__(self, default: float | None, **kwargs: float):
                self.timeout = default
                self.connect = kwargs.get('connect')
                self.read = kwargs.get('read')
                self.write = kwargs.get('write')
                self.pool = kwargs.get('pool')
        HTTPX_DEFAULT_TIMEOUT_CONFIG = HttpxTimeout(5.0)


class PageInfo:
    url: URL | NotGiven # type: ignore
    params: Query | NotGiven
    json: Body | NotGiven

    @overload
    def __init__(self, *, url: URL) -> None: ... # type: ignore
    @overload
    def __init__(self, *, params: Query) -> None: ...
    @overload
    def __init__(self, *, json: Body) -> None: ...
    def __init__(self, *, url: URL | NotGiven = NOT_GIVEN, json: Body | NotGiven = NOT_GIVEN, params: Query | NotGiven = NOT_GIVEN) -> None: # type: ignore
        self.url = url
        self.json = json
        self.params = params

    @override
    def __repr__(self) -> str:
        if not isinstance(self.url, NotGiven): 
            return f"{self.__class__.__name__}(url={self.url})"
        if not isinstance(self.json, NotGiven): 
            return f"{self.__class__.__name__}(json={self.json})"
        return f"{self.__class__.__name__}(params={self.params})"

class BasePage(GenericModel, Generic[_T]):
    _options: FinalRequestOptions = PrivateAttr()
    _model: Type[_T] = PrivateAttr()

    def has_next_page(self) -> bool:
        items = self._get_page_items()
        if not items:
            return False
        return self.next_page_info() is not None

    def next_page_info(self) -> Optional[PageInfo]: ... # type: ignore
    def _get_page_items(self) -> Iterable[_T]: ... # type: ignore
    def _params_from_url(self, url: URL) -> httpx.QueryParams: # type: ignore
        return httpx.QueryParams(cast(Any, self._options.params)).merge(url.params) # type: ignore

    def _info_to_options(self, info: PageInfo) -> FinalRequestOptions:
        options = model_copy(self._options)
        options._strip_raw_response_header()
        if not isinstance(info.params, NotGiven):
            options.params = {**options.params, **info.params} # type: ignore
            return options
        if not isinstance(info.url, NotGiven):
            params = self._params_from_url(info.url)
            url = info.url.copy_with(params=params) # type: ignore
            options.params = dict(url.params) # type: ignore
            options.url = str(url)
            return options
        if not isinstance(info.json, NotGiven):
            if not is_mapping(info.json):
                raise TypeError("Pagination is only supported with mappings")
            if not options.json_data:
                options.json_data = {**info.json}
            else:
                if not is_mapping(options.json_data): # type: ignore
                    raise TypeError("Pagination is only supported with mappings")
                options.json_data = {**options.json_data, **info.json} # type: ignore
            return options
        raise ValueError("Unexpected PageInfo state")

class BaseSyncPage(BasePage[_T], Generic[_T]):
    _client: SyncAPIClient = pydantic.PrivateAttr() # type: ignore

    def _set_private_attributes(self, client: SyncAPIClient, model: Type[_T], options: FinalRequestOptions) -> None: # type: ignore
        if PYDANTIC_V2 and getattr(self, "__pydantic_private__", None) is None:
            self.__pydantic_private__ = {}
        self._model = model
        self._client = client
        self._options = options

    def __iter__(self) -> Iterator[_T]: # type: ignore
        for page in self.iter_pages():
            for item in page._get_page_items():
                yield item

    def iter_pages(self: SyncPageT) -> Iterator[SyncPageT]:
        page = self
        while True:
            yield page
            if page.has_next_page():
                page = page.get_next_page()
            else:
                return

    def get_next_page(self: SyncPageT) -> SyncPageT:
        info = self.next_page_info()
        if not info:
            raise RuntimeError("No next page expected; please check `.has_next_page()` before calling `.get_next_page()`.")
        options = self._info_to_options(info)
        return self._client._request_api_list(self._model, page=self.__class__, options=options) # type: ignore

class AsyncPaginator(Generic[_T, AsyncPageT]):
    def __init__(self, client: AsyncAPIClient, options: FinalRequestOptions, page_cls: Type[AsyncPageT], model: Type[_T]) -> None: # type: ignore
        self._model = model
        self._client = client
        self._options = options
        self._page_cls = page_cls

    def __await__(self) -> Generator[Any, None, AsyncPageT]:
        return self._get_page().__await__()

    async def _get_page(self) -> AsyncPageT:
        def _parser(resp: AsyncPageT) -> AsyncPageT:
            resp._set_private_attributes(model=self._model, options=self._options, client=self._client) # type: ignore
            return resp
        self._options.post_parser = _parser # type: ignore
        return await self._client.request(self._page_cls, self._options) # type: ignore

    async def __aiter__(self) -> AsyncIterator[_T]:
        page = cast(AsyncPageT, await self) # type: ignore
        async for item in page:
            yield item

class BaseAsyncPage(BasePage[_T], Generic[_T]):
    _client: AsyncAPIClient = pydantic.PrivateAttr() # type: ignore

    def _set_private_attributes(self, model: Type[_T], client: AsyncAPIClient, options: FinalRequestOptions) -> None: # type: ignore
        if PYDANTIC_V2 and getattr(self, "__pydantic_private__", None) is None:
            self.__pydantic_private__ = {}
        self._model = model
        self._client = client
        self._options = options

    async def __aiter__(self) -> AsyncIterator[_T]:
        async for page in self.iter_pages():
            for item in page._get_page_items():
                yield item

    async def iter_pages(self: AsyncPageT) -> AsyncIterator[AsyncPageT]:
        page = self
        while True:
            yield page
            if page.has_next_page():
                page = await page.get_next_page()
            else:
                return

    async def get_next_page(self: AsyncPageT) -> AsyncPageT:
        info = self.next_page_info()
        if not info:
            raise RuntimeError("No next page expected; please check `.has_next_page()` before calling `.get_next_page()`.")
        options = self._info_to_options(info)
        return await self._client._request_api_list(self._model, page=self.__class__, options=options) # type: ignore

_HttpxClientT = TypeVar("_HttpxClientT", bound=Union[httpx.Client, aiohttp.ClientSession]) # type: ignore
_DefaultStreamT = TypeVar("_DefaultStreamT", bound=Union[Stream[Any], AsyncStream[Any]])

class BaseClient(Generic[_HttpxClientT, _DefaultStreamT]):
    _client: _HttpxClientT
    _version: str
    _base_url: URL # type: ignore
    max_retries: int
    timeout: Union[float, Timeout, HttpxTimeout, None] # type: ignore
    _strict_response_validation: bool
    _idempotency_header: str | None
    _default_stream_cls: type[_DefaultStreamT] | None = None

    def __init__(self, *, version: str, base_url: str | URL, _strict_response_validation: bool, max_retries: int = DEFAULT_MAX_RETRIES, timeout: float | Timeout | HttpxTimeout | None = DEFAULT_TIMEOUT, custom_headers: Mapping[str, str] | None = None, custom_query: Mapping[str, object] | None = None) -> None: # type: ignore
        self._version = version
        self._base_url = self._enforce_trailing_slash(URL(base_url)) # type: ignore
        self.max_retries = max_retries
        self.timeout = timeout
        self._custom_headers = custom_headers or {}
        self._custom_query = custom_query or {}
        self._strict_response_validation = _strict_response_validation
        self._idempotency_header = None
        self._platform: Platform | None = None # type: ignore
        if max_retries is None: # pyright: ignore[reportUnnecessaryComparison]
            raise TypeError("max_retries cannot be None. If you want to disable retries, pass `0`; if you want unlimited retries, pass `math.inf` or a very high number; if you want the default behavior, pass `openai.DEFAULT_MAX_RETRIES`")

    def _enforce_trailing_slash(self, url: URL) -> URL: # type: ignore
        if url.raw_path.endswith(b"/"): # type: ignore
            return url
        return url.copy_with(raw_path=url.raw_path + b"/") # type: ignore

    def _make_status_error_from_response(self, response: httpx.Response) -> APIStatusError: # type: ignore
        if response.is_closed and not response.is_stream_consumed: # type: ignore
            body = None
            err_msg = f"Error code: {response.status_code}" # type: ignore
        else:
            err_text = response.text.strip() # type: ignore
            body = err_text
            try:
                body = json.loads(err_text)
                err_msg = f"Error code: {response.status_code} - {body}" # type: ignore
            except Exception:
                err_msg = err_text or f"Error code: {response.status_code}" # type: ignore
        return self._make_status_error(err_msg, body=body, response=response)

    def _make_status_error(self, err_msg: str, *, body: object, response: httpx.Response) -> _exceptions.APIStatusError: # type: ignore
        raise NotImplementedError()

    def _build_headers(self, options: FinalRequestOptions, *, retries_taken: int = 0) -> httpx.Headers: # type: ignore
        custom_headers = options.headers or {}
        headers_dict = _merge_mappings(self.default_headers, custom_headers)
        self._validate_headers(headers_dict, custom_headers)
        headers = httpx.Headers(headers_dict) # type: ignore
        idempotency_header = self._idempotency_header
        if idempotency_header and options.idempotency_key and idempotency_header not in headers:
            headers[idempotency_header] = options.idempotency_key
        lower_custom_headers = [header.lower() for header in custom_headers]
        if "x-stainless-retry-count" not in lower_custom_headers:
            headers["x-stainless-retry-count"] = str(retries_taken)
        if "x-stainless-read-timeout" not in lower_custom_headers:
            timeout_val = self.timeout if isinstance(options.timeout, NotGiven) else options.timeout
            read_val = None
            if isinstance(timeout_val, HttpxTimeout): # type: ignore
                read_val = timeout_val.read # type: ignore
            elif isinstance(timeout_val, Timeout): # type: ignore
                read_val = getattr(timeout_val, 'read_timeout', timeout_val if isinstance(timeout_val, (float, int)) else None)

            if read_val is not None:
                headers["x-stainless-read-timeout"] = str(read_val)
        return headers

    def _prepare_url(self, url: str) -> URL: # type: ignore
        merge_url = URL(url) # type: ignore
        if merge_url.is_relative_url: # type: ignore
            merge_raw_path = self.base_url.raw_path + merge_url.raw_path.lstrip(b"/") # type: ignore
            return self.base_url.copy_with(raw_path=merge_raw_path) # type: ignore
        return merge_url

    def _make_sse_decoder(self) -> SSEDecoder | SSEBytesDecoder:
        return SSEDecoder()

    def _build_request(self, options: FinalRequestOptions, *, retries_taken: int = 0) -> httpx.Request: # type: ignore
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Request options: %s", model_dump(options, exclude_unset=True))
        kwargs: dict[str, Any] = {}
        json_data = options.json_data
        if options.extra_json is not None:
            if json_data is None:
                json_data = cast(Body, options.extra_json)
            elif is_mapping(json_data):
                json_data = _merge_mappings(json_data, options.extra_json) # type: ignore
            else:
                raise RuntimeError(f"Unexpected JSON data type, {type(json_data)}, cannot merge with `extra_body`")
        headers = self._build_headers(options, retries_taken=retries_taken)
        params = _merge_mappings(self.default_query, options.params)
        content_type = headers.get("Content-Type")
        files = options.files
        if content_type is not None and content_type.startswith("multipart/form-data"):
            if "boundary" not in content_type:
                headers.pop("Content-Type")
            if json_data:
                if not is_dict(json_data):
                    raise TypeError(f"Expected query input to be a dictionary for multipart requests but got {type(json_data)} instead.")
                kwargs["data"] = self._serialize_multipartform(json_data) # type: ignore
            if not files: # type: ignore
                files = cast(Any, ForceMultipartDict()) 
        prepared_url = self._prepare_url(options.url)
        if "_" in prepared_url.host: # type: ignore
            kwargs["extensions"] = {"sni_hostname": prepared_url.host.replace("_", "-")} # type: ignore
        
        current_timeout = self.timeout if isinstance(options.timeout, NotGiven) else options.timeout
        if not isinstance(current_timeout, HttpxTimeout) and current_timeout is not None : # type: ignore
            if isinstance(current_timeout, (float,int)):
                 current_timeout = HttpxTimeout(current_timeout) # type: ignore
            else: 
                 current_timeout = HttpxTimeout(None) # type: ignore

        return self._client.build_request(headers=headers, timeout=current_timeout, method=options.method, url=prepared_url, params=self.qs.stringify(cast(Mapping[str, Any], params)) if params else None, json=json_data if is_given(json_data) else None, files=files, **kwargs) # type: ignore

    def _serialize_multipartform(self, data: Mapping[object, object]) -> dict[str, object]:
        items = self.qs.stringify_items(data, array_format="brackets") # type: ignore
        serialized: dict[str, object] = {}
        for key, value in items:
            existing = serialized.get(key)
            if not existing:
                serialized[key] = value
                continue
            if is_list(existing):
                existing.append(value)
            else:
                serialized[key] = [existing, value]
        return serialized

    def _maybe_override_cast_to(self, cast_to: type[ResponseT], options: FinalRequestOptions) -> type[ResponseT]:
        if not is_given(options.headers):
            return cast_to
        headers = dict(options.headers) # type: ignore
        override_cast_to = headers.pop(OVERRIDE_CAST_TO_HEADER, NOT_GIVEN)
        if is_given(override_cast_to):
            options.headers = headers # type: ignore
            return cast(Type[ResponseT], override_cast_to)
        return cast_to

    def _should_stream_response_body(self, request: httpx.Request) -> bool: # type: ignore
        return request.headers.get(RAW_RESPONSE_HEADER) == "stream" # type: ignore

    def _process_response_data(self, *, data: object, cast_to: type[ResponseT], response: httpx.Response) -> ResponseT: # type: ignore
        if data is None:
            return cast(ResponseT, None)
        if cast_to is object:
            return cast(ResponseT, data)
        try:
            if inspect.isclass(cast_to) and issubclass(cast_to, ModelBuilderProtocol):
                return cast(ResponseT, cast_to.build(response=response, data=data)) # type: ignore
            if self._strict_response_validation:
                return cast(ResponseT, validate_type(type_=cast_to, value=data))
            return cast(ResponseT, construct_type(type_=cast_to, value=data))
        except pydantic.ValidationError as err: # type: ignore
            raise APIResponseValidationError(response=response, body=data) from err

    @property
    def qs(self) -> Querystring: return Querystring()
    @property
    def custom_auth(self) -> httpx.Auth | None: return None # type: ignore
    @property
    def auth_headers(self) -> dict[str, str]: return {}
    @property
    def default_headers(self) -> dict[str, str | Omit]:
        return {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": self.user_agent, **self.platform_headers(), **self.auth_headers, **self._custom_headers}
    @property
    def default_query(self) -> dict[str, object]: return {**self._custom_query}
    def _validate_headers(self, headers: Headers, custom_headers: Headers) -> None: return # noqa: ARG002
    @property
    def user_agent(self) -> str: return f"{self.__class__.__name__}/Python {self._version}"
    @property
    def base_url(self) -> URL: return self._base_url # type: ignore
    @base_url.setter
    def base_url(self, url: URL | str) -> None: self._base_url = self._enforce_trailing_slash(url if isinstance(url, URL) else URL(url)) # type: ignore
    def platform_headers(self) -> Dict[str, str]: return platform_headers(self._version, platform=self._platform) # type: ignore

    def _parse_retry_after_header(self, response_headers: Optional[httpx.Headers] = None) -> float | None: # type: ignore
        if response_headers is None: return None
        try:
            retry_ms_header = response_headers.get("retry-after-ms", None)
            return float(retry_ms_header) / 1000 # type: ignore
        except (TypeError, ValueError): pass
        retry_header = response_headers.get("retry-after")
        try: return float(retry_header) # type: ignore
        except (TypeError, ValueError): pass
        retry_date_tuple = email.utils.parsedate_tz(retry_header) # type: ignore
        if retry_date_tuple is None: return None
        retry_date = email.utils.mktime_tz(retry_date_tuple)
        return float(retry_date - time.time())

    def _calculate_retry_timeout(self, remaining_retries: int, options: FinalRequestOptions, response_headers: Optional[httpx.Headers] = None) -> float: # type: ignore
        max_retries = options.get_max_retries(self.max_retries)
        retry_after = self._parse_retry_after_header(response_headers)
        if retry_after is not None and 0 < retry_after <= 60: return retry_after
        nb_retries = min(max_retries - remaining_retries, 1000)
        sleep_seconds = min(INITIAL_RETRY_DELAY * pow(2.0, nb_retries), MAX_RETRY_DELAY)
        jitter = 1 - 0.25 * random()
        timeout = sleep_seconds * jitter
        return timeout if timeout >= 0 else 0

    def _should_retry(self, response: httpx.Response) -> bool: # type: ignore
        should_retry_header = response.headers.get("x-should-retry") # type: ignore
        if should_retry_header == "true": log.debug("Retrying as header `x-should-retry` is set to `true`"); return True
        if should_retry_header == "false": log.debug("Not retrying as header `x-should-retry` is set to `false`"); return False
        if response.status_code == 408: log.debug("Retrying due to status code %i", response.status_code); return True # type: ignore
        if response.status_code == 409: log.debug("Retrying due to status code %i", response.status_code); return True # type: ignore
        if response.status_code == 429: log.debug("Retrying due to status code %i", response.status_code); return True # type: ignore
        if response.status_code >= 500: log.debug("Retrying due to status code %i", response.status_code); return True # type: ignore
        log.debug("Not retrying"); return False

    def _idempotency_key(self) -> str: return f"stainless-python-retry-{uuid.uuid4()}"

class _DefaultHttpxClient(httpx.Client): # type: ignore
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        kwargs.setdefault("limits", DEFAULT_CONNECTION_LIMITS)
        kwargs.setdefault("follow_redirects", True)
        super().__init__(**kwargs)

if TYPE_CHECKING: DefaultHttpxClient = httpx.Client # type: ignore
else: DefaultHttpxClient = _DefaultHttpxClient

class SyncHttpxClientWrapper(DefaultHttpxClient): # type: ignore
    def __del__(self) -> None:
        if self.is_closed: return
        try: self.close()
        except Exception: pass

class SyncAPIClient(BaseClient[httpx.Client, Stream[Any]]): # type: ignore
    _client: httpx.Client # type: ignore
    _default_stream_cls: type[Stream[Any]] | None = None
    def __init__(self, *, version: str, base_url: str | URL, max_retries: int = DEFAULT_MAX_RETRIES, timeout: float | Timeout | HttpxTimeout | None | NotGiven = NOT_GIVEN, http_client: httpx.Client | None = None, custom_headers: Mapping[str, str] | None = None, custom_query: Mapping[str, object] | None = None, _strict_response_validation: bool) -> None: # type: ignore
        if not is_given(timeout):
            if http_client and http_client.timeout != HTTPX_DEFAULT_TIMEOUT_CONFIG: timeout = http_client.timeout # type: ignore
            else: timeout = DEFAULT_TIMEOUT
        if http_client is not None and not isinstance(http_client, httpx.Client): raise TypeError(f"Invalid `http_client` argument; Expected an instance of `httpx.Client` but got {type(http_client)}") # type: ignore
        super().__init__(version=version, timeout=cast(Timeout, timeout), base_url=base_url, max_retries=max_retries, custom_query=custom_query, custom_headers=custom_headers, _strict_response_validation=_strict_response_validation) # type: ignore
        self._client = http_client or SyncHttpxClientWrapper(base_url=base_url, timeout=cast(HttpxTimeout, timeout)) # type: ignore
    def is_closed(self) -> bool: return self._client.is_closed # type: ignore
    def close(self) -> None:
        if hasattr(self, "_client"): self._client.close() # type: ignore
    def __enter__(self: _T) -> _T: return self
    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, exc_tb: TracebackType | None) -> None: self.close()
    def _prepare_options(self, options: FinalRequestOptions) -> FinalRequestOptions: return options
    def _prepare_request(self, request: httpx.Request) -> None: return # type: ignore
    @overload
    def request(self, cast_to: Type[ResponseT], options: FinalRequestOptions, *, stream: Literal[True], stream_cls: Type[_StreamT]) -> _StreamT: ...
    @overload
    def request(self, cast_to: Type[ResponseT], options: FinalRequestOptions, *, stream: Literal[False] = False) -> ResponseT: ...
    @overload
    def request(self, cast_to: Type[ResponseT], options: FinalRequestOptions, *, stream: bool = False, stream_cls: Type[_StreamT] | None = None) -> ResponseT | _StreamT: ...
    def request(self, cast_to: Type[ResponseT], options: FinalRequestOptions, *, stream: bool = False, stream_cls: type[_StreamT] | None = None) -> ResponseT | _StreamT:
        cast_to = self._maybe_override_cast_to(cast_to, options)
        input_options = model_copy(options)
        if input_options.idempotency_key is None and input_options.method.lower() != "get": input_options.idempotency_key = self._idempotency_key()
        response: httpx.Response | None = None # type: ignore
        max_retries = input_options.get_max_retries(self.max_retries)
        retries_taken = 0
        for retries_taken in range(max_retries + 1):
            options = model_copy(input_options)
            options = self._prepare_options(options)
            remaining_retries = max_retries - retries_taken
            request = self._build_request(options, retries_taken=retries_taken)
            self._prepare_request(request)
            kwargs: HttpxSendArgs = {} # type: ignore
            if self.custom_auth is not None: kwargs["auth"] = self.custom_auth # type: ignore
            log.debug("Sending HTTP Request: %s %s", request.method, request.url)
            response = None
            try: response = self._client.send(request, stream=stream or self._should_stream_response_body(request=request), **kwargs) # type: ignore
            except httpx.TimeoutException as err: # type: ignore
                log.debug("Encountered httpx.TimeoutException", exc_info=True)
                if remaining_retries > 0: self._sleep_for_retry(retries_taken=retries_taken, max_retries=max_retries, options=input_options, response=None); continue
                log.debug("Raising timeout error"); raise APITimeoutError(request=request) from err
            except Exception as err:
                log.debug("Encountered Exception", exc_info=True)
                if remaining_retries > 0: self._sleep_for_retry(retries_taken=retries_taken, max_retries=max_retries, options=input_options, response=None); continue
                log.debug("Raising connection error"); raise APIConnectionError(request=request) from err
            log.debug('HTTP Response: %s %s "%i %s" %s', request.method, request.url, response.status_code, response.reason_phrase, response.headers) # type: ignore
            log.debug("request_id: %s", response.headers.get("x-request-id")) # type: ignore
            try: response.raise_for_status() # type: ignore
            except httpx.HTTPStatusError as err: # type: ignore
                log.debug("Encountered httpx.HTTPStatusError", exc_info=True)
                if remaining_retries > 0 and self._should_retry(err.response): # type: ignore
                    err.response.close() # type: ignore
                    self._sleep_for_retry(retries_taken=retries_taken, max_retries=max_retries, options=input_options, response=response)
                    continue
                if not err.response.is_closed: err.response.read() # type: ignore
                log.debug("Re-raising status error"); raise self._make_status_error_from_response(err.response) from None # type: ignore
            break
        assert response is not None, "could not resolve response (should never happen)"
        return self._process_response(cast_to=cast_to, options=options, response=response, stream=stream, stream_cls=stream_cls, retries_taken=retries_taken) # type: ignore
    def _sleep_for_retry(self, *, retries_taken: int, max_retries: int, options: FinalRequestOptions, response: httpx.Response | None) -> None: # type: ignore
        remaining_retries = max_retries - retries_taken
        if remaining_retries == 1: log.debug("1 retry left")
        else: log.debug("%i retries left", remaining_retries)
        timeout = self._calculate_retry_timeout(remaining_retries, options, response.headers if response else None) # type: ignore
        log.info("Retrying request to %s in %f seconds", options.url, timeout)
        time.sleep(timeout)
    def _process_response(self, *, cast_to: Type[ResponseT], options: FinalRequestOptions, response: httpx.Response, stream: bool, stream_cls: type[Stream[Any]] | type[AsyncStream[Any]] | None, retries_taken: int = 0) -> ResponseT: # type: ignore
        if response.request.headers.get(RAW_RESPONSE_HEADER) == "true": # type: ignore
            return cast(ResponseT, LegacyAPIResponse(raw=response, client=self, cast_to=cast_to, stream=stream, stream_cls=stream_cls, options=options, retries_taken=retries_taken)) # type: ignore
        origin = get_origin(cast_to) or cast_to
        if inspect.isclass(origin) and issubclass(origin, BaseAPIResponse):
            if not issubclass(origin, APIResponse): raise TypeError(f"API Response types must subclass {APIResponse}; Received {origin}")
            response_cls = cast("type[BaseAPIResponse[Any]]", cast_to)
            return cast(ResponseT, response_cls(raw=response, client=self, cast_to=extract_response_type(response_cls), stream=stream, stream_cls=stream_cls, options=options, retries_taken=retries_taken)) # type: ignore
        if cast_to == httpx.Response: return cast(ResponseT, response) # type: ignore
        api_response = APIResponse(raw=response, client=self, cast_to=cast("type[ResponseT]", cast_to), stream=stream, stream_cls=stream_cls, options=options, retries_taken=retries_taken) # type: ignore
        if bool(response.request.headers.get(RAW_RESPONSE_HEADER)): return cast(ResponseT, api_response) # type: ignore
        return api_response.parse() # type: ignore
    def _request_api_list(self, model: Type[object], page: Type[SyncPageT], options: FinalRequestOptions) -> SyncPageT:
        def _parser(resp: SyncPageT) -> SyncPageT: resp._set_private_attributes(client=self, model=model, options=options); return resp # type: ignore
        options.post_parser = _parser # type: ignore
        return self.request(page, options, stream=False) # type: ignore
    @overload
    def get(self, path: str, *, cast_to: Type[ResponseT], options: RequestOptions = {}, stream: Literal[False] = False) -> ResponseT: ...
    @overload
    def get(self, path: str, *, cast_to: Type[ResponseT], options: RequestOptions = {}, stream: Literal[True], stream_cls: type[_StreamT]) -> _StreamT: ...
    @overload
    def get(self, path: str, *, cast_to: Type[ResponseT], options: RequestOptions = {}, stream: bool, stream_cls: type[_StreamT] | None = None) -> ResponseT | _StreamT: ...
    def get(self, path: str, *, cast_to: Type[ResponseT], options: RequestOptions = {}, stream: bool = False, stream_cls: type[_StreamT] | None = None) -> ResponseT | _StreamT:
        opts = FinalRequestOptions.construct(method="get", url=path, **options) # type: ignore
        return cast(ResponseT, self.request(cast_to, opts, stream=stream, stream_cls=stream_cls))
    @overload
    def post(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, options: RequestOptions = {}, files: RequestFiles | None = None, stream: Literal[False] = False) -> ResponseT: ...
    @overload
    def post(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, options: RequestOptions = {}, files: RequestFiles | None = None, stream: Literal[True], stream_cls: type[_StreamT]) -> _StreamT: ...
    @overload
    def post(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, options: RequestOptions = {}, files: RequestFiles | None = None, stream: bool, stream_cls: type[_StreamT] | None = None) -> ResponseT | _StreamT: ...
    def post(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, options: RequestOptions = {}, files: RequestFiles | None = None, stream: bool = False, stream_cls: type[_StreamT] | None = None) -> ResponseT | _StreamT:
        opts = FinalRequestOptions.construct(method="post", url=path, json_data=body, files=to_httpx_files(files), **options) # type: ignore
        return cast(ResponseT, self.request(cast_to, opts, stream=stream, stream_cls=stream_cls))
    def patch(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, options: RequestOptions = {}) -> ResponseT:
        opts = FinalRequestOptions.construct(method="patch", url=path, json_data=body, **options) # type: ignore
        return self.request(cast_to, opts) # type: ignore
    def put(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, files: RequestFiles | None = None, options: RequestOptions = {}) -> ResponseT:
        opts = FinalRequestOptions.construct(method="put", url=path, json_data=body, files=to_httpx_files(files), **options) # type: ignore
        return self.request(cast_to, opts) # type: ignore
    def delete(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, options: RequestOptions = {}) -> ResponseT:
        opts = FinalRequestOptions.construct(method="delete", url=path, json_data=body, **options) # type: ignore
        return self.request(cast_to, opts) # type: ignore
    def get_api_list(self, path: str, *, model: Type[object], page: Type[SyncPageT], body: Body | None = None, options: RequestOptions = {}, method: str = "get") -> SyncPageT:
        opts = FinalRequestOptions.construct(method=method, url=path, json_data=body, **options) # type: ignore
        return self._request_api_list(model, page, opts)

class AsyncAPIClient(BaseClient[aiohttp.ClientSession, AsyncStream[Any]]):
    _client: aiohttp.ClientSession
    _default_stream_cls: type[AsyncStream[Any]] | None = None
    def __init__(self, *, version: str, base_url: str | URL, _strict_response_validation: bool, max_retries: int = DEFAULT_MAX_RETRIES, timeout: float | Timeout | HttpxTimeout | None | NotGiven = NOT_GIVEN, http_client: aiohttp.ClientSession | None = None, custom_headers: Mapping[str, str] | None = None, custom_query: Mapping[str, object] | None = None) -> None: # type: ignore
        if not is_given(timeout):
            if http_client and hasattr(http_client, "timeout") and http_client.timeout != HTTPX_DEFAULT_TIMEOUT_CONFIG: # type: ignore
                timeout = http_client.timeout # type: ignore
            else: timeout = DEFAULT_TIMEOUT
        if http_client is not None and not isinstance(http_client, aiohttp.ClientSession): raise TypeError(f"Invalid `http_client` argument; Expected an instance of `aiohttp.ClientSession` but got {type(http_client)}")
        super().__init__(version=version, base_url=base_url, timeout=cast(Timeout, timeout), max_retries=max_retries, custom_query=custom_query, custom_headers=custom_headers, _strict_response_validation=_strict_response_validation) # type: ignore
        self.timeout: Union[float, Timeout, HttpxTimeout, None] = cast(Timeout, timeout) # type: ignore
        if http_client is not None: self._client = http_client
        else:
            aiohttp_session_timeout = None
            current_timeout_config = self.timeout
            if isinstance(current_timeout_config, (float, int)): aiohttp_session_timeout = aiohttp.ClientTimeout(total=current_timeout_config)
            elif isinstance(current_timeout_config, HttpxTimeout): # type: ignore
                 total = current_timeout_config.timeout or current_timeout_config.read or current_timeout_config.connect # type: ignore
                 if total: aiohttp_session_timeout = aiohttp.ClientTimeout(total=total)
                 else: aiohttp_session_timeout = aiohttp.ClientTimeout(total=cast(HttpxTimeout,DEFAULT_TIMEOUT).timeout) # type: ignore
            
            self._client = aiohttp.ClientSession(timeout=aiohttp_session_timeout)
    def is_closed(self) -> bool: return self._client.closed
    async def close(self) -> None:
        if hasattr(self, "_client") and not self._client.closed: await self._client.close()
    async def __aenter__(self: _T) -> _T: return self
    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, exc_tb: TracebackType | None) -> None: await self.close()
    async def _prepare_options(self, options: FinalRequestOptions) -> FinalRequestOptions: return options
    async def _prepare_request(self, request: httpx.Request) -> None: return # type: ignore

    async def _build_aiohttp_request_kwargs_with_files(self, options: FinalRequestOptions, *, retries_taken: int = 0) -> Dict[str, Any]:
        if log.isEnabledFor(logging.DEBUG): log.debug("Request options: %s", model_dump(options, exclude_unset=True))
        aiohttp_kwargs: Dict[str, Any] = {}
        json_data = options.json_data
        if options.extra_json is not None:
            if json_data is None: json_data = cast(Body, options.extra_json)
            elif is_mapping(json_data) and is_mapping(options.extra_json): json_data = _merge_mappings(json_data, options.extra_json) # type: ignore
            else: raise RuntimeError(f"Unexpected JSON data type, {type(json_data)}, cannot merge with `extra_body`")
        headers = self._build_headers(options, retries_taken=retries_taken)
        aiohttp_kwargs["headers"] = dict(headers)
        params = _merge_mappings(self.default_query, options.params)
        if params: aiohttp_kwargs["params"] = self.qs.stringify(cast(Mapping[str, Any], params))
        files = options.files
        if files:
            form_data = aiohttp.FormData()
            await self._async_populate_aiohttp_form_data(form_data, files)
            if is_given(json_data):
                if not is_dict(json_data): raise TypeError(f"Expected json_data to be a dictionary for multipart/form-data requests but got {type(json_data)} instead.")
                for key, value in self._serialize_multipartform(cast(Mapping[object, object], json_data)).items():
                    if isinstance(value, list):
                        for item_val in value: form_data.add_field(key, str(item_val))
                    else: form_data.add_field(key, str(value))
                json_data = NOT_GIVEN
            aiohttp_kwargs["data"] = form_data
            if "Content-Type" in aiohttp_kwargs["headers"]: del aiohttp_kwargs["headers"]["Content-Type"]
        if is_given(json_data): aiohttp_kwargs["json"] = json_data
        
        chosen_timeout_config = options.timeout if not isinstance(options.timeout, NotGiven) else self.timeout
        if isinstance(chosen_timeout_config, HttpxTimeout): # type: ignore
            connect_timeout = chosen_timeout_config.connect # type: ignore
            read_timeout = chosen_timeout_config.read # type: ignore
            total_timeout_val = chosen_timeout_config.timeout # type: ignore
            if connect_timeout is None and read_timeout is None and total_timeout_val is not None: aiohttp_kwargs["timeout"] = aiohttp.ClientTimeout(total=total_timeout_val)
            else: aiohttp_kwargs["timeout"] = aiohttp.ClientTimeout(connect=connect_timeout, sock_read=read_timeout)
        elif isinstance(chosen_timeout_config, (int, float)): aiohttp_kwargs["timeout"] = aiohttp.ClientTimeout(total=chosen_timeout_config)
        elif isinstance(chosen_timeout_config, Timeout) and hasattr(chosen_timeout_config, 'read_timeout'): # type: ignore
            aiohttp_kwargs["timeout"] = aiohttp.ClientTimeout(total=chosen_timeout_config.read_timeout) # type: ignore

        aiohttp_kwargs["method"] = options.method.upper()
        if self.custom_auth is not None: log.warning("The `custom_auth` parameter is set but not supported by the aiohttp client. It will be ignored. Please adapt authentication to use headers or other aiohttp-compatible mechanisms.")
        return aiohttp_kwargs

    async def _resolve_file_content_for_aiohttp(self, file_content: FileContent, filename_hint: Optional[str] = None) -> Tuple[Union[bytes, IO[bytes]], Optional[str], Optional[str]]:
        filename = filename_hint
        content_type: Optional[str] = None
        import mimetypes
        if isinstance(file_content, os.PathLike):
            path = anyio.Path(os.fspath(file_content))
            if filename is None: filename = path.name
            content_bytes = await path.read_bytes()
            if filename and not content_type: content_type = mimetypes.guess_type(filename)[0]
            return content_bytes, filename, content_type
        elif isinstance(file_content, (bytes, bytearray)):
            if filename and not content_type: content_type = mimetypes.guess_type(filename)[0]
            return file_content, filename, content_type
        elif isinstance(file_content, io.IOBase):
            if not (hasattr(file_content, "read") and ("b" in getattr(file_content, "mode", "") or isinstance(file_content, io.BytesIO))): raise ValueError("FileIO object must be readable in binary mode.")
            if filename is None and hasattr(file_content, "name") and isinstance(file_content.name, str): filename = os.path.basename(file_content.name)
            if filename and not content_type: content_type = mimetypes.guess_type(filename)[0]
            if hasattr(file_content, 'seekable') and file_content.seekable(): # type: ignore
                if hasattr(file_content, 'tell') and file_content.tell() != 0: file_content.seek(0) # type: ignore
            elif hasattr(file_content, 'tell') and file_content.tell() != 0: log.warning(f"File stream for {filename or 'unknown file'} is not seekable and not at position 0. Upload may send partial or no data.")
            return cast(IO[bytes], file_content), filename, content_type
        else: raise TypeError(f"Unhandled FileContent type: {type(file_content)}")

    async def _async_populate_aiohttp_form_data(self, form: aiohttp.FormData, request_files: RequestFiles) -> None:
        files_to_process: List[Tuple[str, FileTypes]] = []
        if is_mapping_t(request_files): files_to_process.extend(request_files.items()) # type: ignore
        elif is_sequence_t(request_files):
            if len(request_files) > 0 and not isinstance(request_files[0], tuple): raise TypeError(f"Expected sequence of (name, file_obj) tuples for request_files, but got sequence of {type(request_files[0])}") # type: ignore
            files_to_process.extend(cast(Sequence[Tuple[str, FileTypes]], request_files))
        else: raise TypeError(f"Unexpected type for request_files: {type(request_files)}")
        for name, file_obj_union in files_to_process:
            filename: Optional[str] = None
            file_content_actual: FileContent
            explicit_content_type: Optional[str] = None
            if is_file_content(file_obj_union): file_content_actual = file_obj_union # type: ignore
            elif is_tuple_t(file_obj_union) and len(file_obj_union) >= 2: # type: ignore
                filename = file_obj_union[0] # type: ignore
                file_content_actual = file_obj_union[1] # type: ignore
                if not is_file_content(file_content_actual): raise TypeError(f"Invalid FileContent in tuple for key '{name}': {type(file_content_actual)}")
                if len(file_obj_union) > 2 and isinstance(file_obj_union[2], str): explicit_content_type = file_obj_union[2] # type: ignore
            else: raise TypeError(f"Unexpected file object type for key '{name}': {type(file_obj_union)}")
            resolved_content, resolved_filename, guessed_content_type = await self._resolve_file_content_for_aiohttp(file_content_actual, filename_hint=filename)
            final_content_type = explicit_content_type or guessed_content_type
            form.add_field(name, resolved_content, filename=resolved_filename, content_type=final_content_type)

    @overload
    async def request(self, cast_to: Type[ResponseT], options: FinalRequestOptions, *, stream: Literal[False] = False) -> ResponseT: ...
    @overload
    async def request(self, cast_to: Type[ResponseT], options: FinalRequestOptions, *, stream: Literal[True], stream_cls: type[_AsyncStreamT]) -> _AsyncStreamT: ...
    @overload
    async def request(self, cast_to: Type[ResponseT], options: FinalRequestOptions, *, stream: bool, stream_cls: type[_AsyncStreamT] | None = None) -> ResponseT | _AsyncStreamT: ...
    async def request(self, cast_to: Type[ResponseT], options: FinalRequestOptions, *, stream: bool = False, stream_cls: type[_AsyncStreamT] | None = None) -> ResponseT | _AsyncStreamT:
        if self._platform is None: self._platform = await asyncify(get_platform)()
        cast_to = self._maybe_override_cast_to(cast_to, options)
        input_options = model_copy(options)
        if input_options.idempotency_key is None and input_options.method.lower() != "get": input_options.idempotency_key = self._idempotency_key()
        aiohttp_response: aiohttp.ClientResponse | None = None
        max_retries = input_options.get_max_retries(self.max_retries)
        pseudo_request_url_for_error_reporting: URL = self._prepare_url(input_options.url) # type: ignore
        retries_taken = 0
        for retries_taken in range(max_retries + 1):
            current_options = model_copy(input_options)
            current_options = await self._prepare_options(current_options)
            remaining_retries = max_retries - retries_taken
            request_kwargs = await self._build_aiohttp_request_kwargs_with_files(current_options, retries_taken=retries_taken)
            url_for_request = str(pseudo_request_url_for_error_reporting)
            method_for_request = request_kwargs.pop("method")
            log.debug("Sending HTTP Request: %s %s, kwargs: %s", method_for_request, url_for_request, request_kwargs)
            pseudo_request_for_error_reporting = httpx.Request(method=method_for_request, url=url_for_request, headers=request_kwargs.get("headers")) # type: ignore
            try:
                async with self._client.request(method_for_request, url_for_request, **request_kwargs) as aiohttp_response:
                    log.debug('HTTP Response: %s %s "%i %s" %s', method_for_request, url_for_request, aiohttp_response.status, aiohttp_response.reason, aiohttp_response.headers)
                    log.debug("request_id: %s", aiohttp_response.headers.get("x-request-id"))
                    if not (200 <= aiohttp_response.status < 300):
                        await aiohttp_response.read() # Ensure body is read for error construction
                        temp_httpx_response = self._build_temporary_httpx_response(aiohttp_response, pseudo_request_for_error_reporting) # type: ignore
                        if remaining_retries > 0 and self._should_retry(temp_httpx_response): # type: ignore
                            await aiohttp_response.release()
                            await self._sleep_for_retry(retries_taken=retries_taken, max_retries=max_retries, options=input_options, response=temp_httpx_response) # type: ignore
                            continue
                        raise self._make_status_error_from_response(temp_httpx_response) from None # type: ignore
                    break 
            except (aiohttp.ClientConnectionError, aiohttp.ClientHttpProxyError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as err:
                log.debug("Encountered connection/timeout error with aiohttp", exc_info=True)
                if remaining_retries > 0:
                    await self._sleep_for_retry(retries_taken=retries_taken, max_retries=max_retries, options=input_options, response=None)
                    continue
                log.debug("Raising APIConnectionError or APITimeoutError for aiohttp error")
                if isinstance(err, (asyncio.TimeoutError, aiohttp.ServerTimeoutError)): raise APITimeoutError(request=pseudo_request_for_error_reporting) from err
                raise APIConnectionError(request=pseudo_request_for_error_reporting) from err
            except Exception as err:
                log.debug("Encountered generic Exception with aiohttp", exc_info=True)
                if remaining_retries > 0:
                    await self._sleep_for_retry(retries_taken=retries_taken, max_retries=max_retries, options=input_options, response=None)
                    continue
                raise APIConnectionError(request=pseudo_request_for_error_reporting) from err
        assert aiohttp_response is not None, "aiohttp_response should be set if loop completes"
        
        response_to_process: Union[httpx.Response, aiohttp.ClientResponse] # type: ignore
        if stream:
            response_to_process = aiohttp_response
        else:
            response_to_process = await self._convert_aiohttp_response_to_httpx_like(aiohttp_response, pseudo_request_for_error_reporting) # type: ignore
            # _convert_aiohttp_response_to_httpx_like calls .release()

        return await self._process_response(cast_to=cast_to, options=current_options, response=response_to_process, stream=stream, stream_cls=stream_cls, retries_taken=retries_taken) # type: ignore

    async def _sleep_for_retry(self, *, retries_taken: int, max_retries: int, options: FinalRequestOptions, response: httpx.Response | None) -> None: # type: ignore
        remaining_retries = max_retries - retries_taken
        if remaining_retries == 1: log.debug("1 retry left")
        else: log.debug("%i retries left", remaining_retries)
        timeout = self._calculate_retry_timeout(remaining_retries, options, response.headers if response else None) # type: ignore
        log.info("Retrying request to %s in %f seconds", options.url, timeout)
        await anyio.sleep(timeout)

    async def _process_response(self, *, cast_to: Type[ResponseT], options: FinalRequestOptions, response: Union[httpx.Response, aiohttp.ClientResponse], stream: bool, stream_cls: type[Stream[Any]] | type[AsyncStream[Any]] | None, retries_taken: int = 0) -> ResponseT: # type: ignore
        raw_response_header_value: Optional[str] = None
        if isinstance(response, httpx.Response): # type: ignore
            raw_response_header_value = response.request.headers.get(RAW_RESPONSE_HEADER) if hasattr(response, 'request') and response.request else None # type: ignore
        elif isinstance(response, aiohttp.ClientResponse):
            if response.request_info and response.request_info.headers:
                raw_response_header_value = response.request_info.headers.get(RAW_RESPONSE_HEADER)
        
        if raw_response_header_value == "true":
            return cast(ResponseT, LegacyAPIResponse(raw=response, client=self, cast_to=cast_to, stream=stream, stream_cls=stream_cls, options=options, retries_taken=retries_taken)) # type: ignore
        
        origin = get_origin(cast_to) or cast_to
        if inspect.isclass(origin) and issubclass(origin, BaseAPIResponse):
            if not issubclass(origin, AsyncAPIResponse): raise TypeError(f"API Response types must subclass {AsyncAPIResponse}; Received {origin}")
            response_cls = cast("type[BaseAPIResponse[Any]]", cast_to)
            return cast("ResponseT", response_cls(raw=response, client=self, cast_to=extract_response_type(response_cls), stream=stream, stream_cls=stream_cls, options=options, retries_taken=retries_taken)) # type: ignore
        
        if cast_to == httpx.Response: # type: ignore
            if isinstance(response, httpx.Response): return cast(ResponseT, response) # type: ignore
            log.warning("Attempting to cast aiohttp.ClientResponse to httpx.Response in _process_response. This might be unintended for streaming.")
            return cast(ResponseT, response) # This will return aiohttp.ClientResponse if that's what it is.

        api_response = AsyncAPIResponse(raw=response, client=self, cast_to=cast("type[ResponseT]", cast_to), stream=stream, stream_cls=stream_cls, options=options, retries_taken=retries_taken) # type: ignore
        if bool(raw_response_header_value): return cast(ResponseT, api_response)
        return await api_response.parse() # type: ignore

    def _build_temporary_httpx_response(self, aiohttp_response: aiohttp.ClientResponse, request_for_error_reporting: httpx.Request) -> httpx.Response: # type: ignore
        try: content_bytes = aiohttp_response._body if hasattr(aiohttp_response, '_body') and aiohttp_response._body is not None else b""
        except RuntimeError: content_bytes = b""
        temp_response = httpx.Response(status_code=aiohttp_response.status, headers=dict(aiohttp_response.headers), content=content_bytes, request=request_for_error_reporting) # type: ignore
        return temp_response

    async def _convert_aiohttp_response_to_httpx_like(self, aiohttp_response: aiohttp.ClientResponse, request_obj: httpx.Request) -> httpx.Response: # type: ignore
        response_content = await aiohttp_response.read()
        httpx_like_response = httpx.Response(status_code=aiohttp_response.status, headers=dict(aiohttp_response.headers), content=response_content, request=request_obj) # type: ignore
        if not aiohttp_response.closed: await aiohttp_response.release()
        return httpx_like_response

    def _request_api_list(self, model: Type[_T], page: Type[AsyncPageT], options: FinalRequestOptions) -> AsyncPaginator[_T, AsyncPageT]:
        return AsyncPaginator(client=self, options=options, page_cls=page, model=model) # type: ignore
    @overload
    async def get(self, path: str, *, cast_to: Type[ResponseT], options: RequestOptions = {}, stream: Literal[False] = False) -> ResponseT: ...
    @overload
    async def get(self, path: str, *, cast_to: Type[ResponseT], options: RequestOptions = {}, stream: Literal[True], stream_cls: type[_AsyncStreamT]) -> _AsyncStreamT: ...
    @overload
    async def get(self, path: str, *, cast_to: Type[ResponseT], options: RequestOptions = {}, stream: bool, stream_cls: type[_AsyncStreamT] | None = None) -> ResponseT | _AsyncStreamT: ...
    async def get(self, path: str, *, cast_to: Type[ResponseT], options: RequestOptions = {}, stream: bool = False, stream_cls: type[_AsyncStreamT] | None = None) -> ResponseT | _AsyncStreamT:
        opts = FinalRequestOptions.construct(method="get", url=path, **options) # type: ignore
        return await self.request(cast_to, opts, stream=stream, stream_cls=stream_cls)
    @overload
    async def post(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, files: RequestFiles | None = None, options: RequestOptions = {}, stream: Literal[False] = False) -> ResponseT: ...
    @overload
    async def post(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, files: RequestFiles | None = None, options: RequestOptions = {}, stream: Literal[True], stream_cls: type[_AsyncStreamT]) -> _AsyncStreamT: ...
    @overload
    async def post(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, files: RequestFiles | None = None, options: RequestOptions = {}, stream: bool, stream_cls: type[_AsyncStreamT] | None = None) -> ResponseT | _AsyncStreamT: ...
    async def post(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, files: RequestFiles | None = None, options: RequestOptions = {}, stream: bool = False, stream_cls: type[_AsyncStreamT] | None = None) -> ResponseT | _AsyncStreamT:
        opts = FinalRequestOptions.construct(method="post", url=path, json_data=body, files=files, **options) # type: ignore
        return await self.request(cast_to, opts, stream=stream, stream_cls=stream_cls)
    async def patch(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, options: RequestOptions = {}) -> ResponseT:
        opts = FinalRequestOptions.construct(method="patch", url=path, json_data=body, **options) # type: ignore
        return await self.request(cast_to, opts)
    async def put(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, files: RequestFiles | None = None, options: RequestOptions = {}) -> ResponseT:
        opts = FinalRequestOptions.construct(method="put", url=path, json_data=body, files=files, **options) # type: ignore
        return await self.request(cast_to, opts)
    async def delete(self, path: str, *, cast_to: Type[ResponseT], body: Body | None = None, options: RequestOptions = {}) -> ResponseT:
        opts = FinalRequestOptions.construct(method="delete", url=path, json_data=body, **options) # type: ignore
        return await self.request(cast_to, opts)
    def get_api_list(self, path: str, *, model: Type[_T], page: Type[AsyncPageT], body: Body | None = None, options: RequestOptions = {}, method: str = "get") -> AsyncPaginator[_T, AsyncPageT]:
        opts = FinalRequestOptions.construct(method=method, url=path, json_data=body, **options) # type: ignore
        return self._request_api_list(model, page, opts)

def make_request_options(*, query: Query | None = None, extra_headers: Headers | None = None, extra_query: Query | None = None, extra_body: Body | None = None, idempotency_key: str | None = None, timeout: float | httpx.Timeout | None | NotGiven = NOT_GIVEN, post_parser: PostParser | NotGiven = NOT_GIVEN) -> RequestOptions: # type: ignore
    options: RequestOptions = {}
    if extra_headers is not None: options["headers"] = extra_headers
    if extra_body is not None: options["extra_json"] = cast(AnyMapping, extra_body)
    if query is not None: options["params"] = query
    if extra_query is not None: options["params"] = {**options.get("params", {}), **extra_query} # type: ignore
    if not isinstance(timeout, NotGiven): options["timeout"] = timeout
    if idempotency_key is not None: options["idempotency_key"] = idempotency_key
    if is_given(post_parser): options["post_parser"] = post_parser # type: ignore
    return options

class ForceMultipartDict(Dict[str, None]):
    def __bool__(self) -> bool: return True

class OtherPlatform:
    def __init__(self, name: str) -> None: self.name = name
    @override
    def __str__(self) -> str: return f"Other:{self.name}"

Platform = Union[OtherPlatform, Literal["MacOS", "Linux", "Windows", "FreeBSD", "OpenBSD", "iOS", "Android", "Unknown"]]

def get_platform() -> Platform:
    try:
        system = platform.system().lower()
        platform_name = platform.platform().lower()
    except Exception: return "Unknown"
    if "iphone" in platform_name or "ipad" in platform_name: return "iOS"
    if system == "darwin": return "MacOS"
    if system == "windows": return "Windows"
    if "android" in platform_name: return "Android"
    if system == "linux":
        distro_id = distro.id() # type: ignore
        if distro_id == "freebsd": return "FreeBSD"
        if distro_id == "openbsd": return "OpenBSD"
        return "Linux"
    if platform_name: return OtherPlatform(platform_name)
    return "Unknown"

@lru_cache(maxsize=None)
def platform_headers(version: str, *, platform: Platform | None) -> Dict[str, str]:
    return {"X-Stainless-Lang": "python", "X-Stainless-Package-Version": version, "X-Stainless-OS": str(platform or get_platform()), "X-Stainless-Arch": str(get_architecture()), "X-Stainless-Runtime": get_python_runtime(), "X-Stainless-Runtime-Version": get_python_version()}

class OtherArch:
    def __init__(self, name: str) -> None: self.name = name
    @override
    def __str__(self) -> str: return f"other:{self.name}"

Arch = Union[OtherArch, Literal["x32", "x64", "arm", "arm64", "unknown"]]

def get_python_runtime() -> str:
    try: return platform.python_implementation()
    except Exception: return "unknown"
def get_python_version() -> str:
    try: return platform.python_version()
    except Exception: return "unknown"
def get_architecture() -> Arch:
    try: machine = platform.machine().lower()
    except Exception: return "unknown"
    if machine in ("arm64", "aarch64"): return "arm64"
    if machine == "arm": return "arm"
    if machine == "x86_64": return "x64"
    if sys.maxsize <= 2**32: return "x32"
    if machine: return OtherArch(machine)
    return "unknown"

def _merge_mappings(obj1: Mapping[_T_co, Union[_T, Omit]], obj2: Mapping[_T_co, Union[_T, Omit]]) -> Dict[_T_co, _T]:
    merged = {**obj1, **obj2}
    return {key: value for key, value in merged.items() if not isinstance(value, Omit)}
