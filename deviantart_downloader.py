#!/usr/bin/env python3
"""
Descargador de galerías de DeviantArt usando la API pública oficial.

Requisitos:
  1. Tener una cuenta de DeviantArt.
  2. Registrar una aplicación en https://www.deviantart.com/developers/register
     (tipo "confidential") para obtener un client_id y client_secret.
  3. pip install requests

Uso:
  cp .env.example .env   # y rellena DA_CLIENT_ID / DA_CLIENT_SECRET
  python deviantart_downloader.py https://www.deviantart.com/nombreusuario
  python deviantart_downloader.py nombreusuario

  # o pasando las credenciales por argumento:
  python deviantart_downloader.py <url_perfil> --client-id XXX --client-secret YYY
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse, unquote

try:
    import requests
except ImportError:
    sys.exit("Falta la librería 'requests'. Instálala con: pip install requests")

def load_dotenv(path: Path | None = None):
    """Carga variables desde un archivo .env sin sobrescribir las ya definidas."""
    env_file = path or Path(__file__).resolve().parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    """Lee un entero desde una variable de entorno, con valor por defecto."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        sys.exit(f"El valor de {name} debe ser un número entero, no: {value!r}")


API_BASE = "https://www.deviantart.com/api/v1/oauth2"
TOKEN_URL = "https://www.deviantart.com/oauth2/token"
USER_AGENT = "da-gallery-downloader/1.0"
PAGE_LIMIT = 24  # máximo permitido por la API


class DeviantArtClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()

    def _ensure_token(self, force: bool = False):
        with self._token_lock:
            if force or time.time() >= self._token_expiry:
                self._refresh_token()

    def _refresh_token(self):
        resp = self.session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            sys.exit(
                f"Error al obtener el token OAuth ({resp.status_code}): {resp.text}\n"
                "Verifica tu client_id y client_secret."
            )
        data = resp.json()
        self.session.headers["Authorization"] = f"Bearer {data['access_token']}"
        # renovamos 60 s antes de que expire (expira en 1 hora)
        self._token_expiry = time.time() + data.get("expires_in", 3600) - 60

    def api_get(self, endpoint: str, params: dict | None = None) -> dict:
        """GET a la API con renovación automática de token y reintentos."""
        self._ensure_token()

        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        for attempt in range(5):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 401:
                self._ensure_token(force=True)
                continue
            if resp.status_code == 429:
                wait = 2 ** (attempt + 2)
                print(f"  Límite de peticiones alcanzado, esperando {wait} s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Demasiados reintentos fallidos para {url}")


def extract_username(profile_url: str) -> str:
    """Extrae el nombre de usuario de una URL de perfil de DeviantArt."""
    parsed = urlparse(profile_url if "://" in profile_url else f"https://{profile_url}")
    host = parsed.netloc.lower()

    # Formato antiguo: https://usuario.deviantart.com
    m = re.match(r"^([a-z0-9-]+)\.deviantart\.com$", host)
    if m and m.group(1) != "www":
        return m.group(1)

    # Formato actual: https://www.deviantart.com/usuario[/...]
    if "deviantart.com" in host:
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            return parts[0]

    # Si pasaron directamente el nombre de usuario
    if re.match(r"^[A-Za-z0-9.-]+$", profile_url) and "." not in profile_url:
        return profile_url

    sys.exit(f"No pude extraer un nombre de usuario de: {profile_url}")


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "sin_titulo"


def guess_extension(url: str) -> str:
    path = unquote(urlparse(url).path)
    ext = os.path.splitext(path)[1].lower()
    return ext if ext and len(ext) <= 5 else ".jpg"


class DownloadManifest:
    """Registro persistente de deviations ya descargadas (por deviationid).

    Permite detectar duplicados entre ejecuciones aunque el título de la obra
    (y por tanto el nombre del archivo) haya cambiado.
    """

    def __init__(self, out_dir: Path):
        self.path = out_dir / "_downloaded.json"
        self._lock = threading.Lock()
        self._entries: dict[str, str] = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._entries = {str(k): str(v) for k, v in data.items()}
            except (json.JSONDecodeError, OSError):
                print(f"  AVISO: no pude leer {self.path.name}, se regenerará.")
        self._seed_from_existing_files(out_dir)

    def _seed_from_existing_files(self, out_dir: Path):
        """Registra archivos descargados por versiones anteriores del script
        (nombre con sufijo _<8 primeros chars del deviationid>)."""
        pattern = re.compile(r"_([0-9A-Fa-f]{8})$")
        for f in out_dir.iterdir():
            if not f.is_file() or f.name.startswith("_") or f.suffix == ".part":
                continue
            m = pattern.search(f.stem)
            if m:
                self._entries.setdefault(m.group(1).upper(), f.name)

    def _key(self, dev_id: str) -> str:
        return dev_id[:8].upper()

    def has(self, dev_id: str) -> bool:
        with self._lock:
            return self._key(dev_id) in self._entries

    def filename_for(self, dev_id: str) -> str | None:
        with self._lock:
            return self._entries.get(self._key(dev_id))

    def add(self, dev_id: str, filename: str):
        with self._lock:
            self._entries[self._key(dev_id)] = filename
            self._save_locked()

    def _save_locked(self):
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)


def fetch_gallery(client: DeviantArtClient, username: str) -> list[dict]:
    """Recorre todas las páginas de gallery/all y devuelve las deviations."""
    deviations = []
    offset = 0
    while True:
        data = client.api_get(
            "gallery/all",
            params={
                "username": username,
                "offset": offset,
                "limit": PAGE_LIMIT,
                "mature_content": "true",
            },
        )
        results = data.get("results", [])
        deviations.extend(results)
        print(f"  Página con offset {offset}: {len(results)} obras (total: {len(deviations)})")
        if not data.get("has_more"):
            break
        offset = data.get("next_offset") or offset + PAGE_LIMIT
    return deviations


def download_file(session: requests.Session, url: str, dest: Path) -> bool:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with session.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        tmp.rename(dest)
        return True
    except Exception as e:
        tmp.unlink(missing_ok=True)
        print(f"  ERROR descargando {url}: {e}")
        return False


def process_deviation(
    client: DeviantArtClient, dev: dict, out_dir: Path, delay: float, manifest: DownloadManifest
) -> tuple[str, str]:
    """Resuelve la URL del archivo y lo descarga. Devuelve (estado, descripción)."""
    title = dev.get("title") or "sin_titulo"
    dev_id = dev.get("deviationid", "")

    # Duplicado: ya se descargó en una ejecución anterior (aunque haya
    # cambiado el título). Se comprueba antes de llamar a la API.
    if dev_id and manifest.has(dev_id):
        existing = manifest.filename_for(dev_id)
        if existing and (out_dir / existing).is_file():
            return "skipped", f"Ya existe, omitido: {existing}"
        # El archivo fue borrado manualmente: lo volvemos a descargar.

    # 1) Preferimos el archivo original si el autor permite descargarlo
    file_url = None
    if dev.get("is_downloadable"):
        try:
            dl = client.api_get(f"deviation/download/{dev_id}")
            file_url = dl.get("src")
        except Exception:
            pass  # caemos al content.src

    # 2) Si no, la imagen a máxima resolución disponible públicamente
    if not file_url:
        content = dev.get("content") or {}
        file_url = content.get("src")

    if not file_url:
        # Literatura, diarios, etc. no tienen archivo multimedia
        return "no_media", f"SIN ARCHIVO (literatura/diario): {title}"

    ext = guess_extension(file_url)
    dest = out_dir / f"{sanitize_filename(title)}_{dev_id[:8]}{ext}"

    if dest.exists():
        if dev_id:
            manifest.add(dev_id, dest.name)
        return "skipped", f"Ya existe, omitido: {dest.name}"

    ok = download_file(client.session, file_url, dest)
    if delay:
        time.sleep(delay)
    if ok:
        if dev_id:
            manifest.add(dev_id, dest.name)
        return "downloaded", f"Descargado: {dest.name}"
    return "failed", f"FALLÓ: {dest.name}"


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Descarga toda la galería de un perfil de DeviantArt usando la API oficial."
    )
    parser.add_argument(
        "profile_url",
        metavar="perfil",
        help="URL del perfil (https://www.deviantart.com/usuario) o solo el nombre de usuario",
    )
    parser.add_argument("-o", "--output", default="downloads", help="Carpeta de salida (default: downloads)")
    parser.add_argument("--client-id", default=os.environ.get("DA_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("DA_CLIENT_SECRET"))
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Pausa en segundos tras cada descarga, por hilo (default: 0.5)")
    parser.add_argument("-w", "--workers", type=int, default=env_int("DA_WORKERS", 4),
                        help="Descargas simultáneas (default: DA_WORKERS del .env o 4, "
                             "recomendado no superar 8)")
    args = parser.parse_args()

    if args.workers < 1:
        sys.exit(f"El número de workers debe ser al menos 1 (recibido: {args.workers}).")

    if not args.client_id or not args.client_secret:
        sys.exit(
            "Faltan credenciales de la API.\n"
            "Regístrate en https://www.deviantart.com/developers/register y luego:\n"
            "  export DA_CLIENT_ID='...'\n"
            "  export DA_CLIENT_SECRET='...'"
        )

    username = extract_username(args.profile_url)
    print(f"Usuario: {username}")

    client = DeviantArtClient(args.client_id, args.client_secret)

    print("Obteniendo listado de la galería...")
    deviations = fetch_gallery(client, username)
    if not deviations:
        sys.exit("La galería está vacía o el usuario no existe.")
    print(f"\nTotal de obras encontradas: {len(deviations)}\n")

    out_dir = Path(args.output) / username
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = DownloadManifest(out_dir)

    # Guardamos los metadatos completos por si se necesitan después
    with open(out_dir / "_metadata.json", "w", encoding="utf-8") as f:
        json.dump(deviations, f, ensure_ascii=False, indent=2)

    counts = {"downloaded": 0, "skipped": 0, "failed": 0, "no_media": 0}
    total = len(deviations)
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_deviation, client, dev, out_dir, args.delay, manifest): dev
            for dev in deviations
        }
        for future in as_completed(futures):
            done += 1
            try:
                status, message = future.result()
            except Exception as e:
                status, message = "failed", f"ERROR inesperado: {e}"
            counts[status] += 1
            print(f"[{done}/{total}] {message}")

    downloaded, skipped, failed, no_media = (
        counts["downloaded"], counts["skipped"], counts["failed"], counts["no_media"]
    )
    print(
        f"\nListo. Descargadas: {downloaded} | Omitidas (ya existían): {skipped} "
        f"| Sin archivo: {no_media} | Fallidas: {failed}"
    )
    print(f"Archivos guardados en: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
