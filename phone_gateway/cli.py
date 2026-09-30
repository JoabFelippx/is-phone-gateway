import argparse

import uvicorn

from phone_gateway.converters import MAX_FRAME_BYTES


def main():
    parser = argparse.ArgumentParser(description="Gateway de sensores do celular para AMQP")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--cert", help="Certificado HTTPS confiável pelo celular")
    parser.add_argument("--key", help="Chave privada do certificado HTTPS")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error("Informe --cert e --key juntos.")
    uvicorn.run(
        "phone_gateway.app:app",
        host=args.host,
        port=args.port,
        ssl_certfile=args.cert,
        ssl_keyfile=args.key,
        ws_max_size=MAX_FRAME_BYTES + 8,
        ws_max_queue=8,
    )


if __name__ == "__main__":
    main()
