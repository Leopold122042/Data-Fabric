"""Installation-scoped, keyed pseudonyms for explicitly approved identifiers."""
import hashlib
import hmac
import os
import secrets
import unicodedata
from pathlib import Path

from platform_core.storage import DomainError


def _key(store):
    path = Path(store.path).parent / 'pseudonym.key'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    if os.name != 'nt':
        os.chmod(path, 0o600)
    key = path.read_bytes()
    if len(key) != 32:
        raise DomainError('本地伪名密钥格式无效，拒绝输出患者标识', 503)
    return key


class PatientPseudonymizer:
    def __init__(self, store):
        self.key = _key(store)

    def token(self, value):
        """Return a stable, non-reversible token; never persist or return the raw ID."""
        if value is None:
            return None
        if type(value) not in (str, int) or not str(value).strip() or len(str(value)) > 256:
            raise DomainError('患者标识值格式不支持，拒绝生成伪名', 403)
        canonical = unicodedata.normalize('NFKC', str(value).strip()).encode('utf-8')
        message = b'medfabric:patient-token:v1\0' + canonical
        return 'pt_' + hmac.new(self.key, message, hashlib.sha256).hexdigest()


def patient_token(value, store):
    """Convenience wrapper for callers handling a single value."""
    return PatientPseudonymizer(store).token(value)
