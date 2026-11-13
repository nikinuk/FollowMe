"""
Armazenamento de contas e cifras por usuário.

Backends, escolhidos automaticamente:
- usuário logado + GCS_BUCKET configurado -> Google Cloud Storage
  (gs://<bucket>/usuarios/<id>/conta.txt e .../cifras/<arquivo>.txt)
- usuário logado sem bucket (dev local)   -> pasta local usuarios/<id>/
- anônimo                                 -> pasta local musicas/ (comportamento antigo)
"""
import os
import re
import hashlib
import unicodedata

try:
    from google.cloud import storage as _gcs
except ImportError:
    _gcs = None


def normalize_user_id(email: str) -> str:
    """Converte o e-mail em um id seguro para usar como nome de pasta/prefixo."""
    email = unicodedata.normalize("NFKD", email.strip().lower())
    email = email.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9._-]+", "_", email).strip("._-") or "usuario"


def hash_password(password: str) -> str:
    # autenticação propositalmente simples: hash sem salt guardado junto aos dados
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class UserStorage:
    def __init__(self, musicas_dir: str, local_users_dir: str):
        self.musicas_dir = musicas_dir
        self.local_users_dir = local_users_dir
        self.bucket = None
        bucket_name = os.environ.get("GCS_BUCKET", "repositorio-usuario").strip()
        if bucket_name:
            if _gcs is None:
                print("GCS_BUCKET definido, mas google-cloud-storage nao esta instalado; usando pasta local.")
            else:
                try:
                    self.bucket = _gcs.Client().bucket(bucket_name)
                    print(f"Armazenamento de usuarios: gs://{bucket_name}")
                except Exception as e:
                    print(f"Falha ao abrir o bucket {bucket_name} ({e}); usando pasta local.")

    def _use_bucket(self, user) -> bool:
        return user is not None and self.bucket is not None

    def _blob(self, user: str, *parts: str):
        return self.bucket.blob("usuarios/" + user + "/" + "/".join(parts))

    def _local_path(self, user: str, *parts: str) -> str:
        return os.path.join(self.local_users_dir, user, *parts)

    # ----- conta -----
    def get_password_hash(self, user: str):
        """Hash da senha do usuário, ou None se a conta ainda não existe."""
        if self._use_bucket(user):
            blob = self._blob(user, "conta.txt")
            try:
                if not blob.exists():
                    return None
                return blob.download_as_text(encoding="utf-8").strip()
            except Exception as e:
                print(f"Erro lendo conta de {user} no bucket: {e}")
                return None
        path = self._local_path(user, "conta.txt")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()

    def set_password_hash(self, user: str, pw_hash: str):
        if self._use_bucket(user):
            self._blob(user, "conta.txt").upload_from_string(pw_hash, content_type="text/plain")
            return
        path = self._local_path(user, "conta.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(pw_hash)

    # ----- cifras -----
    def _cifra_local_path(self, user, name: str) -> str:
        if user is None:
            return os.path.join(self.musicas_dir, name)
        return self._local_path(user, "cifras", name)

    def write_cifra(self, user, name: str, content: str):
        if self._use_bucket(user):
            self._blob(user, "cifras", name).upload_from_string(
                content, content_type="text/plain; charset=utf-8"
            )
            return
        path = self._cifra_local_path(user, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def read_cifra(self, user, name: str):
        """Conteúdo da cifra, ou None se não existe/não pôde ser lida."""
        if self._use_bucket(user):
            try:
                blob = self._blob(user, "cifras", name)
                if not blob.exists():
                    return None
                return blob.download_as_text(encoding="utf-8")
            except Exception as e:
                print(f"Erro lendo cifra {name} de {user} no bucket: {e}")
                return None
        path = self._cifra_local_path(user, name)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def list_cifras(self, user):
        """Lista [{'arquivo', 'title', 'author'}] das cifras .txt do usuário."""
        cifras = []
        if self._use_bucket(user):
            prefix = f"usuarios/{user}/cifras/"
            try:
                blobs = self.bucket.client.list_blobs(self.bucket, prefix=prefix)
                for blob in blobs:
                    name = blob.name[len(prefix):]
                    if not name.endswith(".txt") or "/" in name:
                        continue
                    try:
                        lines = blob.download_as_text(encoding="utf-8").splitlines()
                    except Exception:
                        continue
                    cifras.append(self._entry(name, lines))
            except Exception as e:
                print(f"Erro listando cifras de {user} no bucket: {e}")
            return sorted(cifras, key=lambda c: c["arquivo"])

        base = self.musicas_dir if user is None else self._local_path(user, "cifras")
        if not os.path.isdir(base):
            return []
        for name in sorted(os.listdir(base)):
            if not name.endswith(".txt"):
                continue
            try:
                with open(os.path.join(base, name), "r", encoding="utf-8") as f:
                    lines = [f.readline(), f.readline()]
            except OSError:
                continue
            cifras.append(self._entry(name, lines))
        return cifras

    @staticmethod
    def _entry(name, lines):
        return {
            "arquivo": name,
            "title": lines[0].strip() if len(lines) > 0 else name,
            "author": lines[1].strip() if len(lines) > 1 else "",
        }
