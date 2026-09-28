"""Cria uma CA local e um certificado HTTPS para o IP do gateway."""

import argparse
import ipaddress
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True, help="IP local do computador que executa o gateway")
    parser.add_argument("--output", type=Path, default=Path("certs"))
    args = parser.parse_args()
    ip = str(ipaddress.ip_address(args.ip))
    if not shutil.which("openssl"):
        parser.error("Instale o OpenSSL para gerar os certificados.")
    args.output.mkdir(parents=True, exist_ok=True)
    paths = {
        name: args.output / name
        for name in (
            "rootCA.pem",
            "rootCA-key.pem",
            "gateway.pem",
            "gateway-key.pem",
            "gateway.csr",
        )
    }
    if any(path.exists() for path in paths.values()):
        parser.error("Já existem certificados neste diretório. Escolha outro --output.")
    # As chaves privadas nascem com permissão 0600 em sistemas POSIX.
    previous_umask = os.umask(0o077)
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-nodes",
                "-keyout",
                str(paths["rootCA-key.pem"]),
                "-out",
                str(paths["rootCA.pem"]),
                "-days",
                "3650",
                "-subj",
                "/CN=Phone Gateway Local CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-nodes",
                "-keyout",
                str(paths["gateway-key.pem"]),
                "-out",
                str(paths["gateway.csr"]),
                "-subj",
                "/CN=Phone Gateway",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with tempfile.TemporaryDirectory(prefix="phone-gateway-cert-") as temporary:
            extensions = Path(temporary) / "extensions.cnf"
            extensions.write_text(
                f"subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost\n"
                "basicConstraints=critical,CA:FALSE\n"
                "keyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-in",
                    str(paths["gateway.csr"]),
                    "-CA",
                    str(paths["rootCA.pem"]),
                    "-CAkey",
                    str(paths["rootCA-key.pem"]),
                    "-set_serial",
                    "0x" + os.urandom(16).hex(),
                    "-out",
                    str(paths["gateway.pem"]),
                    "-days",
                    "365",
                    "-sha256",
                    "-extfile",
                    str(extensions),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    finally:
        os.umask(previous_umask)
    print(f"Certificado criado para https://{ip}:8443")
    print(f"Instale SOMENTE {paths['rootCA.pem']} como CA confiável no celular.")
    print("Mantenha os arquivos *-key.pem privados no servidor.")
    print(f"phone-gateway --cert {paths['gateway.pem']} --key {paths['gateway-key.pem']}")


if __name__ == "__main__":
    main()
