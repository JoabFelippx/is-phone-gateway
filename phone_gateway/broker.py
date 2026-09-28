import asyncio
from concurrent.futures import ThreadPoolExecutor

from is_wire.core import Channel, Message


class BrokerPublisher:
    """Um único thread possui o canal AMQP; nunca compartilha sockets entre threads."""

    def __init__(self, uri, exchange, *, stream=False, channel_factory=Channel):
        self.uri = uri
        self.exchange = exchange
        self.stream = stream
        self.channel_factory = channel_factory
        self.channel = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="phone-amqp")

    async def _run(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, function, *args)

    def _connect(self):
        self.channel = self.channel_factory(
            self.uri,
            exchange=self.exchange,
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5,
            heartbeat=30,
            reconnect=False,
        )

    async def connect(self):
        await self._run(self._connect)

    def _publish(self, content, topic, metadata):
        message = Message(content=content)
        message.metadata.update(metadata)
        if self.stream:
            self.channel.publish_stream(message, topic=topic)
        else:
            self.channel.publish(message, topic=topic)
        return len(message.body)

    async def publish(self, content, topic, metadata):
        return await self._run(self._publish, content, topic, metadata)

    def _heartbeat(self):
        # Leia também os heartbeats recebidos: só enviar não atualiza bytes_recv.
        try:
            self.channel.connection.drain_events(timeout=0.01)
        except TimeoutError:
            pass
        self.channel.connection.heartbeat_tick()

    async def heartbeat(self):
        if self.channel is not None:
            await self._run(self._heartbeat)

    async def close(self):
        try:
            if self.channel is not None:
                await self._run(self.channel.close)
                self.channel = None
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)
