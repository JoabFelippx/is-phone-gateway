import asyncio
import threading

import pytest
from is_msgs.common_pb2 import Vector3
from is_wire.core import ContentType

from phone_gateway.broker import BrokerPublisher


@pytest.mark.parametrize("stream", [False, True])
def test_channel_lifecycle_publish_and_received_heartbeats_use_one_worker(stream):
    calls = []

    class FakeChannel:
        def __init__(self, uri, **kwargs):
            self.connection = self
            calls.append(("connect", threading.get_ident(), kwargs))

        def publish(self, message, topic):
            calls.append(("publish", threading.get_ident(), message))
            assert topic == "phone.vector"

        def publish_stream(self, message, topic):
            calls.append(("stream", threading.get_ident(), message))
            assert topic == "phone.vector"

        def drain_events(self, timeout):
            calls.append(("drain", threading.get_ident(), timeout))
            raise TimeoutError()

        def heartbeat_tick(self):
            calls.append(("heartbeat", threading.get_ident(), None))

        def close(self):
            calls.append(("close", threading.get_ident(), None))

    async def exercise():
        publisher = BrokerPublisher(
            "amqp://broker", "is", stream=stream, channel_factory=FakeChannel
        )
        await publisher.connect()
        assert await publisher.publish(Vector3(x=1), "phone.vector", {"device_id": "test"}) > 0
        await publisher.heartbeat()
        await publisher.close()

    main_thread = threading.get_ident()
    asyncio.run(exercise())
    assert len({call[1] for call in calls}) == 1 and calls[0][1] != main_thread
    assert [call[0] for call in calls] == [
        "connect",
        "stream" if stream else "publish",
        "drain",
        "heartbeat",
        "close",
    ]
    assert calls[1][2].content_type == ContentType.PROTOBUF
    assert calls[1][2].metadata["device_id"] == "test"
    assert calls[0][2]["reconnect"] is False
