# Note: initially copied from https://github.com/florimondmanca/httpx-sse/blob/master/src/httpx_sse/_decoders.py
from __future__ import annotations

import json
import inspect
from types import TracebackType
from typing import TYPE_CHECKING, Any, Generic, TypeVar, Iterator, AsyncIterator, cast, Union # Added Union
from typing_extensions import Self, Protocol, TypeGuard, override, get_origin, runtime_checkable

import httpx
import aiohttp # Added

from ._utils import is_mapping, extract_type_var_from_base
from ._exceptions import APIError

if TYPE_CHECKING:
    from ._client import OpenAI, AsyncOpenAI
    from httpx import Request as HttpxRequest # For APIError
    from aiohttp.helpers import SimpleCookie # For type hinting if needed
    from yarl import URL as YarlURL # For type hinting if needed


_T = TypeVar("_T")


class Stream(Generic[_T]):
    """Provides the core interface to iterate over a synchronous stream response."""

    response: httpx.Response

    _decoder: SSEBytesDecoder

    def __init__(
        self,
        *,
        cast_to: type[_T],
        response: httpx.Response,
        client: OpenAI,
    ) -> None:
        self.response = response
        self._cast_to = cast_to
        self._client = client
        self._decoder = client._make_sse_decoder()
        self._iterator = self.__stream__()

    def __next__(self) -> _T:
        return self._iterator.__next__()

    def __iter__(self) -> Iterator[_T]:
        for item in self._iterator:
            yield item

    def _iter_events(self) -> Iterator[ServerSentEvent]:
        yield from self._decoder.iter_bytes(self.response.iter_bytes())

    def __stream__(self) -> Iterator[_T]:
        cast_to = cast(Any, self._cast_to)
        response = self.response
        process_data = self._client._process_response_data
        iterator = self._iter_events()

        for sse in iterator:
            if sse.data.startswith("[DONE]"):
                break

            if sse.event is None or sse.event.startswith("response.") or sse.event.startswith("transcript."):
                data = sse.json()
                if is_mapping(data) and data.get("error"):
                    message = None
                    error = data.get("error")
                    if is_mapping(error):
                        message = error.get("message")
                    if not message or not isinstance(message, str):
                        message = "An error occurred during streaming"

                    raise APIError(
                        message=message,
                        request=self.response.request,
                        body=data["error"],
                    )

                yield process_data(data=data, cast_to=cast_to, response=response)

            else:
                data = sse.json()

                if sse.event == "error" and is_mapping(data) and data.get("error"):
                    message = None
                    error = data.get("error")
                    if is_mapping(error):
                        message = error.get("message")
                    if not message or not isinstance(message, str):
                        message = "An error occurred during streaming"

                    raise APIError(
                        message=message,
                        request=self.response.request,
                        body=data["error"],
                    )

                yield process_data(data={"data": data, "event": sse.event}, cast_to=cast_to, response=response)

        # Ensure the entire stream is consumed
        for _sse in iterator:
            ...

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """
        Close the response and release the connection.

        Automatically called if the response body is read to completion.
        """
        self.response.close()


class AsyncStream(Generic[_T]):
    """Provides the core interface to iterate over an asynchronous stream response."""

    response: Union[httpx.Response, aiohttp.ClientResponse] # Changed
    _cast_to: type[_T]
    _client: AsyncOpenAI # type: ignore
    _decoder: SSEDecoder | SSEBytesDecoder

    def __init__(
        self,
        *,
        cast_to: type[_T],
        response: Union[httpx.Response, aiohttp.ClientResponse], # Changed
        client: AsyncOpenAI, # type: ignore
    ) -> None:
        self.response = response
        self._cast_to = cast_to
        self._client = client
        self._decoder = client._make_sse_decoder()
        self._iterator = self.__stream__()

    async def __anext__(self) -> _T:
        return await self._iterator.__anext__()

    async def __aiter__(self) -> AsyncIterator[_T]:
        async for item in self._iterator:
            yield item

    async def _iter_events(self) -> AsyncIterator[ServerSentEvent]:
        if isinstance(self.response, httpx.Response):
            async for sse in self._decoder.aiter_bytes(self.response.aiter_bytes()):
                yield sse
        elif isinstance(self.response, aiohttp.ClientResponse):
            async for sse in self._decoder.aiter_bytes(self.response.content.iter_any()): # type: ignore
                yield sse
        else:
            # Handle cases where response might be an unexpected type, though type hints should prevent this.
            raise TypeError(f"Unexpected response type: {type(self.response)}")


    async def __stream__(self) -> AsyncIterator[_T]:
        cast_to = cast(Any, self._cast_to)
        # The `response` argument for `process_data` and `APIError` expects `httpx.Response`.
        # This is a point of friction. For now, we might need to construct a mock or partial
        # httpx.Response if the actual one is aiohttp.ClientResponse, or adapt downstream.
        # For this step, we'll focus on getting the stream data.
        # The `APIError` request object will be problematic.
        
        # Construct a pseudo httpx.Request for APIError
        pseudo_request: Union[HttpxRequest, None] = None # type: ignore
        if isinstance(self.response, httpx.Response):
            pseudo_request = self.response.request
        elif isinstance(self.response, aiohttp.ClientResponse):
            # Create a minimal httpx.Request object for compatibility with APIError
            pseudo_request = httpx.Request( # type: ignore
                method=self.response.method, # type: ignore
                url=str(self.response.url), # type: ignore
                headers=self.response.headers, # type: ignore
            )
            
        # The `response` passed to process_data also needs to be httpx.Response.
        # This implies that if we fully switch to aiohttp.ClientResponse for streaming,
        # _process_response_data and APIError need to be adapted, or we use a compatibility wrapper here.
        
        pseudo_request: Union[HttpxRequest, None] = None # type: ignore
        response_for_processing: httpx.Response # For process_data and APIError

        if isinstance(self.response, httpx.Response):
            pseudo_request = self.response.request
            response_for_processing = self.response
        elif isinstance(self.response, aiohttp.ClientResponse):
            pseudo_request = httpx.Request( # type: ignore
                method=self.response.method, # type: ignore
                url=str(self.response.url), # type: ignore
                headers=self.response.headers, # type: ignore
            )
            # Create a minimal httpx.Response for compatibility with process_data / APIError
            # This is a stop-gap. Content is not being set here as it's streamed.
            response_for_processing = httpx.Response( # type: ignore
                status_code=self.response.status, # type: ignore
                headers=self.response.headers, # type: ignore
                request=pseudo_request
            )
        else:
            # Should not happen due to type hints
            raise TypeError(f"Unexpected response type in AsyncStream: {type(self.response)}")

        process_data = self._client._process_response_data 
        iterator = self._iter_events()

        async for sse in iterator:
            if sse.data.startswith("[DONE]"):
                break

            if sse.event is None or sse.event.startswith("response.") or sse.event.startswith("transcript."):
                data = sse.json()
                if is_mapping(data) and data.get("error"):
                    message = None
                    error_data = data.get("error")
                    if is_mapping(error_data):
                        message = error_data.get("message")
                    if not message or not isinstance(message, str):
                        message = "An error occurred during streaming"
                    raise APIError(message=message, request=pseudo_request, body=error_data)

                yield process_data(data=data, cast_to=cast_to, response=response_for_processing)

            else:
                data = sse.json()
                if sse.event == "error" and is_mapping(data) and data.get("error"):
                    message = None
                    error_data = data.get("error")
                    if is_mapping(error_data):
                        message = error_data.get("message")
                    if not message or not isinstance(message, str):
                        message = "An error occurred during streaming"
                    raise APIError(message=message, request=pseudo_request, body=error_data)
                
                yield process_data(data={"data": data, "event": sse.event}, cast_to=cast_to, response=response_for_processing)

        # Ensure the entire stream is consumed
        async for _sse in iterator:
            ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """
        Close the response and release the connection.

        Automatically called if the response body is read to completion.
        """
        if isinstance(self.response, httpx.Response):
            await self.response.aclose()
        elif isinstance(self.response, aiohttp.ClientResponse):
            if not self.response.closed:
                self.response.release() # For aiohttp, release the connection if not closed.


class ServerSentEvent:
    def __init__(
        self,
        *,
        event: str | None = None,
        data: str | None = None,
        id: str | None = None,
        retry: int | None = None,
    ) -> None:
        if data is None:
            data = ""

        self._id = id
        self._data = data
        self._event = event or None
        self._retry = retry

    @property
    def event(self) -> str | None:
        return self._event

    @property
    def id(self) -> str | None:
        return self._id

    @property
    def retry(self) -> int | None:
        return self._retry

    @property
    def data(self) -> str:
        return self._data

    def json(self) -> Any:
        return json.loads(self.data)

    @override
    def __repr__(self) -> str:
        return f"ServerSentEvent(event={self.event}, data={self.data}, id={self.id}, retry={self.retry})"


class SSEDecoder:
    _data: list[str]
    _event: str | None
    _retry: int | None
    _last_event_id: str | None

    def __init__(self) -> None:
        self._event = None
        self._data = []
        self._last_event_id = None
        self._retry = None

    def iter_bytes(self, iterator: Iterator[bytes]) -> Iterator[ServerSentEvent]:
        """Given an iterator that yields raw binary data, iterate over it & yield every event encountered"""
        for chunk in self._iter_chunks(iterator):
            # Split before decoding so splitlines() only uses \r and \n
            for raw_line in chunk.splitlines():
                line = raw_line.decode("utf-8")
                sse = self.decode(line)
                if sse:
                    yield sse

    def _iter_chunks(self, iterator: Iterator[bytes]) -> Iterator[bytes]:
        """Given an iterator that yields raw binary data, iterate over it and yield individual SSE chunks"""
        data = b""
        for chunk in iterator:
            for line in chunk.splitlines(keepends=True):
                data += line
                if data.endswith((b"\r\r", b"\n\n", b"\r\n\r\n")):
                    yield data
                    data = b""
        if data:
            yield data

    async def aiter_bytes(self, iterator: AsyncIterator[bytes]) -> AsyncIterator[ServerSentEvent]:
        """Given an iterator that yields raw binary data, iterate over it & yield every event encountered"""
        async for chunk in self._aiter_chunks(iterator):
            # Split before decoding so splitlines() only uses \r and \n
            for raw_line in chunk.splitlines():
                line = raw_line.decode("utf-8")
                sse = self.decode(line)
                if sse:
                    yield sse

    async def _aiter_chunks(self, iterator: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """Given an iterator that yields raw binary data, iterate over it and yield individual SSE chunks"""
        data = b""
        async for chunk in iterator:
            for line in chunk.splitlines(keepends=True):
                data += line
                if data.endswith((b"\r\r", b"\n\n", b"\r\n\r\n")):
                    yield data
                    data = b""
        if data:
            yield data

    def decode(self, line: str) -> ServerSentEvent | None:
        # See: https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation  # noqa: E501

        if not line:
            if not self._event and not self._data and not self._last_event_id and self._retry is None:
                return None

            sse = ServerSentEvent(
                event=self._event,
                data="\n".join(self._data),
                id=self._last_event_id,
                retry=self._retry,
            )

            # NOTE: as per the SSE spec, do not reset last_event_id.
            self._event = None
            self._data = []
            self._retry = None

            return sse

        if line.startswith(":"):
            return None

        fieldname, _, value = line.partition(":")

        if value.startswith(" "):
            value = value[1:]

        if fieldname == "event":
            self._event = value
        elif fieldname == "data":
            self._data.append(value)
        elif fieldname == "id":
            if "\0" in value:
                pass
            else:
                self._last_event_id = value
        elif fieldname == "retry":
            try:
                self._retry = int(value)
            except (TypeError, ValueError):
                pass
        else:
            pass  # Field is ignored.

        return None


@runtime_checkable
class SSEBytesDecoder(Protocol):
    def iter_bytes(self, iterator: Iterator[bytes]) -> Iterator[ServerSentEvent]:
        """Given an iterator that yields raw binary data, iterate over it & yield every event encountered"""
        ...

    def aiter_bytes(self, iterator: AsyncIterator[bytes]) -> AsyncIterator[ServerSentEvent]:
        """Given an async iterator that yields raw binary data, iterate over it & yield every event encountered"""
        ...


def is_stream_class_type(typ: type) -> TypeGuard[type[Stream[object]] | type[AsyncStream[object]]]:
    """TypeGuard for determining whether or not the given type is a subclass of `Stream` / `AsyncStream`"""
    origin = get_origin(typ) or typ
    return inspect.isclass(origin) and issubclass(origin, (Stream, AsyncStream))


def extract_stream_chunk_type(
    stream_cls: type,
    *,
    failure_message: str | None = None,
) -> type:
    """Given a type like `Stream[T]`, returns the generic type variable `T`.

    This also handles the case where a concrete subclass is given, e.g.
    ```py
    class MyStream(Stream[bytes]):
        ...

    extract_stream_chunk_type(MyStream) -> bytes
    ```
    """
    from ._base_client import Stream, AsyncStream

    return extract_type_var_from_base(
        stream_cls,
        index=0,
        generic_bases=cast("tuple[type, ...]", (Stream, AsyncStream)),
        failure_message=failure_message,
    )
