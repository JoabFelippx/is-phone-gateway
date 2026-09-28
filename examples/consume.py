"""Consome uma das mensagens publicadas pelo celular com is-wire-sea."""

import argparse
import os

from google.protobuf.json_format import MessageToJson
from is_msgs.common_pb2 import Vector3
from is_msgs.image_pb2 import Image
from is_msgs.power_pb2 import PowerInfo
from is_msgs.ros_pb2 import ROSMessage
from is_wire.core import Channel, Subscription

SCHEMAS = {
    "camera": Image,
    "accelerometer": Vector3,
    "gyroscope": Vector3,
    "orientation": ROSMessage,
    "gps": ROSMessage,
    "battery": PowerInfo,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--broker", default=os.getenv("BROKER_URI"), help="URI AMQP ou use BROKER_URI"
    )
    parser.add_argument("--sensor", required=True, choices=SCHEMAS)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--image-output", default="frame.jpg")
    args = parser.parse_args()
    if not args.broker:
        parser.error("Informe --broker ou BROKER_URI.")
    with Channel(args.broker) as channel:
        subscription = Subscription(channel)
        subscription.subscribe(args.topic)
        print(f"Aguardando mensagens em {args.topic}. Ctrl+C para sair.")
        while True:
            message = channel.consume()
            content = message.unpack(SCHEMAS[args.sensor])
            if args.sensor == "camera":
                with open(args.image_output, "wb") as output:
                    output.write(content.data)
                print(f"Quadro {content.sequence}: {len(content.data)} bytes → {args.image_output}")
            else:
                print(MessageToJson(content))
            print("Metadados:", message.metadata)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
