import os
import shutil
from pathlib import Path
from typing import BinaryIO
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

UPLOADS_DIR = BASE_DIR / "uploads"
os.makedirs(UPLOADS_DIR, exist_ok=True)

STORAGE_TYPE = os.getenv("STORAGE_TYPE", "local").strip().lower()
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "casa-de-lio-uploads")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_USE_SSL = os.getenv("S3_USE_SSL", "false").lower() in ("true", "1", "yes")

_s3_client = None

def obter_cliente_s3():
    """Retorna cliente S3 configurado para MinIO ou AWS S3."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            region_name=S3_REGION,
            use_ssl=S3_USE_SSL
        )
    return _s3_client

def garantir_bucket_s3(cliente=None):
    """Garante que o bucket exista no MinIO/S3."""
    client = cliente or obter_cliente_s3()
    try:
        client.head_bucket(Bucket=S3_BUCKET_NAME)
    except ClientError:
        try:
            client.create_bucket(Bucket=S3_BUCKET_NAME)
        except Exception as e:
            print(f"[Storage S3 Warning] Erro ao criar bucket {S3_BUCKET_NAME}: {e}")

def salvar_arquivo(conteudo: bytes | BinaryIO, nome_arquivo: str, content_type: str = "application/octet-stream") -> str:
    """
    Salva o arquivo no provedor configurado (Local ou S3/MinIO).
    Retorna o nome do arquivo gravado.
    """
    if STORAGE_TYPE == "s3":
        cliente = obter_cliente_s3()
        garantir_bucket_s3(cliente)
        if isinstance(conteudo, bytes):
            cliente.put_object(
                Bucket=S3_BUCKET_NAME,
                Key=nome_arquivo,
                Body=conteudo,
                ContentType=content_type
            )
        else:
            cliente.upload_fileobj(
                Fileobj=conteudo,
                Bucket=S3_BUCKET_NAME,
                Key=nome_arquivo,
                ExtraArgs={"ContentType": content_type}
            )
        return nome_arquivo
    else:
        destino = UPLOADS_DIR / nome_arquivo
        if isinstance(conteudo, bytes):
            with open(destino, "wb") as f:
                f.write(conteudo)
        else:
            with open(destino, "wb") as f:
                shutil.copyfileobj(conteudo, f)
        return nome_arquivo

def remover_arquivo(nome_arquivo: str) -> bool:
    """Remove o arquivo do armazenamento (Local ou S3/MinIO)."""
    if not nome_arquivo:
        return False

    sucesso = False
    if STORAGE_TYPE == "s3":
        try:
            cliente = obter_cliente_s3()
            cliente.delete_object(Bucket=S3_BUCKET_NAME, Key=nome_arquivo)
            sucesso = True
        except Exception as e:
            print(f"[Storage S3 Erro] Falha ao remover {nome_arquivo}: {e}")
    else:
        destino = UPLOADS_DIR / nome_arquivo
        if destino.exists():
            try:
                os.remove(destino)
                sucesso = True
            except Exception as e:
                print(f"[Storage Local Erro] Falha ao remover {destino}: {e}")
    return sucesso

def obter_url_arquivo(nome_arquivo: str) -> str:
    """
    Gera a URL pública ou assinada para acesso ao arquivo.
    No modo S3, gera uma presigned URL temporária de 1 hora.
    No modo local, retorna a URL estática /uploads/<nome_arquivo>.
    """
    if not nome_arquivo:
        return ""

    if STORAGE_TYPE == "s3":
        try:
            cliente = obter_cliente_s3()
            url = cliente.generate_presigned_url(
                "get_object",
                Params={"Bucket": S3_BUCKET_NAME, "Key": nome_arquivo},
                ExpiresIn=3600  # 1 hora de validade
            )
            return url
        except Exception as e:
            print(f"[Storage S3 Erro] Falha ao gerar URL assinada: {e}")
            return f"/uploads/{nome_arquivo}"
    else:
        return f"/uploads/{nome_arquivo}"

def obter_conteudo_arquivo(nome_arquivo: str) -> bytes | None:
    """Retorna os bytes do arquivo para download ou leitura direta."""
    if not nome_arquivo:
        return None

    if STORAGE_TYPE == "s3":
        try:
            cliente = obter_cliente_s3()
            resp = cliente.get_object(Bucket=S3_BUCKET_NAME, Key=nome_arquivo)
            return resp["Body"].read()
        except Exception as e:
            print(f"[Storage S3 Erro] Falha ao obter bytes de {nome_arquivo}: {e}")
            return None
    else:
        destino = UPLOADS_DIR / nome_arquivo
        if destino.exists():
            with open(destino, "rb") as f:
                return f.read()
        return None
