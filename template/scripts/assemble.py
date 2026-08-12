#!/usr/bin/env python3
"""Assemble compose services from the containers repository.

Clones or copies from the containers repo, preserving directory structure
so that relative paths (configuration/, certificates/) inside each service's
docker-compose.yml resolve correctly.
"""

import os
import shutil
import subprocess
import sys
from typing import Any

REPO_URL = "https://github.com/xrhstosmour/containers.git"
# Pin to a specific commit instead of floating on the default branch tip.
# Bump this deliberately when the containers repo needs to be updated.
REPO_COMMIT = "1b3ae03609525e35544967671f14a7308d653b47"


def run_openssl(*args: Any) -> None:  # noqa: ANN401
    subprocess.run(
        ["openssl"] + list(args),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def generate_certs(cert_dir, project_slug) -> None:
    """Generate self-signed certificates and remove template instruction files."""
    print(f"  Generating certificates in {cert_dir}...")
    template_files = [f for f in os.listdir(cert_dir) if f.startswith("template.")]

    # Detect service type from the certificate directory path.
    if "rabbitmq" in cert_dir:
        # RabbitMQ: CA + server certificate chain.
        ca_key = os.path.join(cert_dir, "ca_key.pem")
        ca_crt = os.path.join(cert_dir, "ca_certificate.pem")
        srv_key = os.path.join(cert_dir, "server_key.pem")
        srv_req = os.path.join(cert_dir, "server_request.pem")
        srv_crt = os.path.join(cert_dir, "server_certificate.pem")
        subj = (
            f"/C=GR/L=Athens"
            f"/O={project_slug}.self_signed"
            f"/CN={project_slug}.self_signed.com"
        )
        run_openssl("genrsa", "-out", ca_key, "2048")
        run_openssl(
            "req",
            "-new",
            "-x509",
            "-days",
            "36500",
            "-key",
            ca_key,
            "-out",
            ca_crt,
            "-subj",
            subj,
        )
        run_openssl("genrsa", "-out", srv_key, "2048")
        run_openssl("req", "-new", "-key", srv_key, "-out", srv_req, "-subj", subj)
        run_openssl(
            "x509",
            "-req",
            "-days",
            "36500",
            "-in",
            srv_req,
            "-CA",
            ca_crt,
            "-CAkey",
            ca_key,
            "-set_serial",
            "01",
            "-out",
            srv_crt,
        )
    else:
        # Traefik / generic: single self-signed certificate.
        key_file = os.path.join(cert_dir, "certificate.key")
        csr_file = os.path.join(cert_dir, "certificate.csr")
        crt_file = os.path.join(cert_dir, "certificate.crt")
        subj = f"/C=GR/ST=Athens/L=Athens/O=Local/CN={project_slug}.local"
        run_openssl("ecparam", "-genkey", "-name", "secp384r1", "-out", key_file)
        run_openssl("req", "-new", "-key", key_file, "-out", csr_file, "-subj", subj)
        run_openssl(
            "req",
            "-x509",
            "-sha256",
            "-nodes",
            "-days",
            "730",
            "-key",
            key_file,
            "-in",
            csr_file,
            "-out",
            crt_file,
            "-extensions",
            "v3_req",
            "-addext",
            f"subjectAltName=DNS:{project_slug}.local,DNS:www.{project_slug}.local",
        )

    for f in template_files:
        os.remove(os.path.join(cert_dir, f))
        print(f"  Removed {f}")


MAP = {
    # name -> path inside containers repo
    "postgresql": "databases/postgresql",
    "redis": "databases/redis",
    "rabbitmq": "distribute/brokers/rabbitmq",
    "traefik": "networking/proxies/traefik",
    "glitchtip": "monitoring/codebase/glitchtip",
    "metabase": "databases/manage/metabase",
    "pgadmin4": "databases/manage/pgadmin4",
    "mailpit": "email/mailpit",
    "redis_commander": "databases/manage/redis_commander",
    "flower": "monitoring/workers/flower",
    "minio": "storage/minio",
}


def read_services(containers_directory) -> list[str]:
    """Parse docker-compose.yml include paths to determine which services to copy."""
    services = ["postgresql", "redis", "rabbitmq"]
    compose_file = "docker-compose.yml"
    if not os.path.isfile(compose_file):
        return services
    with open(compose_file) as f:
        for line in f:
            line = line.strip()
            if line.startswith("- containers/") and line.endswith(
                "/docker-compose.yml"
            ):
                path = line.replace("- containers/", "").replace(
                    "/docker-compose.yml", ""
                )
                for name, repo_path in MAP.items():
                    if repo_path == path and name not in services:
                        services.append(name)
    return services


def main() -> None:
    containers_directory = "containers"
    services = read_services(containers_directory)

    # Find the containers repo: check adjacent path first, then cached
    # clone at /tmp/containers, then clone fresh if neither exists.
    containers_root = os.path.abspath("../containers")
    if os.path.isdir(containers_root):
        print(f"Using containers repo at {containers_root}")
    elif os.path.isdir("/tmp/containers"):
        print("Using cached containers repo at /tmp/containers")
        containers_root = "/tmp/containers"
    else:
        # Remove any stale directory before cloning so this step is
        # idempotent even when a previous run left /tmp/containers behind.
        shutil.rmtree("/tmp/containers", ignore_errors=True)
        print(f"Cloning {REPO_URL} @ {REPO_COMMIT} ...")
        os.makedirs("/tmp/containers")
        subprocess.run(["git", "init"], check=True, cwd="/tmp/containers")
        subprocess.run(
            ["git", "remote", "add", "origin", REPO_URL],
            check=True,
            cwd="/tmp/containers",
        )
        subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", REPO_COMMIT],
            check=True,
            cwd="/tmp/containers",
        )
        subprocess.run(
            ["git", "checkout", "FETCH_HEAD"],
            check=True,
            cwd="/tmp/containers",
        )
        containers_root = "/tmp/containers"

    # Copy service directories.
    for name in services:
        src = os.path.join(containers_root, MAP[name])
        dst = os.path.join(containers_directory, MAP[name])
        if not os.path.isdir(src):
            print(f"Error: {MAP[name]} not found in containers repo")
            if name in ("postgresql", "redis", "rabbitmq"):
                sys.exit(1)
            print(f"  Skipping optional service '{name}'")
            continue
        print(f"  Copying {MAP[name]} -> {dst}")
        shutil.copytree(src, dst, dirs_exist_ok=True)

    # Rename all template.* files (template.env -> .env, etc.),
    # except in the certificates directory which we handle separately.
    for root, _, files in os.walk(containers_directory):
        if "certificates" in root:
            continue
        for f in files:
            if f.startswith("template."):
                src_path = os.path.join(root, f)
                name = f[len("template.") :]
                if "secrets" in root and not name.startswith("."):
                    name = "." + name
                dst_path = os.path.join(root, name)
                print(f"  Renaming {src_path} -> {dst_path}")
                os.rename(src_path, dst_path)

    # Read project slug from generated .env.example.
    project_slug = "yap"
    env_example = os.path.join(os.getcwd(), ".env.example")
    if os.path.isfile(env_example):
        with open(env_example) as f:
            for line in f:
                if line.startswith("POSTGRESQL_USER="):
                    project_slug = line.split("=", 1)[1].strip()
                    break

    # Generate htpasswd for Traefik dashboard basic auth.
    for root, _, files in os.walk(containers_directory):
        for f in files:
            if f == ".htpasswd":
                htpasswd_path = os.path.join(root, f)
                with open(htpasswd_path) as hf:
                    if "encoded_password" in hf.read():
                        password = project_slug + "-" + os.urandom(4).hex()
                        result = subprocess.run(
                            ["openssl", "passwd", "-6", password],
                            capture_output=True,
                            text=True,
                        )
                        if result.returncode == 0:
                            with open(htpasswd_path, "w") as hf:
                                hf.write(f"admin:{result.stdout.strip()}\n")
                            print("  Generated htpasswd")
                        else:
                            print(
                                "  Warning: openssl not available, "
                                "htpasswd left as placeholder"
                            )

    # Generate self-signed certificates for all services.
    for root, dirs, _ in os.walk(containers_directory):
        if os.path.basename(root) == "certificates":
            generate_certs(root, project_slug)

    # Propagate the RabbitMQ CA cert to Flower so it can trust the broker over TLS.
    # Must run after generate_certs() so ca_certificate.pem actually exists.
    if "flower" in services:
        rabbit_cert = os.path.join(
            containers_directory, MAP["rabbitmq"], "certificates", "ca_certificate.pem"
        )
        flower_cert_dir = os.path.join(
            containers_directory, MAP["flower"], "certificates"
        )
        if os.path.isfile(rabbit_cert):
            os.makedirs(flower_cert_dir, exist_ok=True)
            shutil.copy2(
                rabbit_cert, os.path.join(flower_cert_dir, "ca_certificate.pem")
            )
            print("  Copied RabbitMQ CA cert to Flower")

    # Write SSL_CERTIFICATE_PATH so Python's ssl module trusts the self-signed CA.
    certs_env = os.path.join(containers_directory, ".certificates")
    ca_cert = os.path.abspath(
        os.path.join(
            containers_directory, MAP["rabbitmq"], "certificates", "ca_certificate.pem"
        )
    )
    with open(certs_env, "w") as f:
        f.write(f"SSL_CERTIFICATE_PATH={ca_cert}\n")
    print(f"  Wrote {certs_env}")

    # Keep /tmp/containers around so subsequent runs of assemble.py within the
    # same copier update can reuse it instead of cloning again.
    # The temp directory is cleaned up by synchronize.sh before the next update.

    print("Done.")


if __name__ == "__main__":
    main()
