# -*- coding: utf-8 -*-
"""
dash_app/services/auth_service.py
Gestor de autenticación RBAC para Plotly Dash.
Autentica contra datos/usuarios.json (mismo archivo que usa gestionar_usuarios.py
y el Streamlit original) con hash SHA-256 + salt de archivos_proyecto/.env.
Usuario desconocido o password incorrecto = acceso denegado. Sin fallbacks,
sin auto-admisión, sin passwords en texto plano.
"""
from pathlib import Path
import json
import hashlib
import os

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
USUARIOS_JSON = BASE_DIR / "datos" / "usuarios.json"

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / "archivos_proyecto" / ".env")
except ImportError:
    pass

SALT = os.getenv("SALT_PASSWORD")
if not SALT:
    raise RuntimeError(
        "SALT_PASSWORD no está definido (archivos_proyecto/.env). "
        "No se puede iniciar autenticación sin salt configurado."
    )

ROLES_PERMISOS = {
    "admin": {
        "nombre": "Administrador",
        "secciones": [
            "Resumen", "Sobregasto PDV / CECO", "Detalle Cuenta", "Detalle PDV", "Detalle CECO", "Contraloria", "Cierres",
            "Temporalidad", "Comparación de Versiones", "Tabla Dinámica", "Cashi AI Assistant", "Gestión de Usuarios"
        ],
        "permite_descargar": True,
        "permite_admin": True,
    },
    "supervisor": {
        "nombre": "Supervisor / Dirección",
        "secciones": [
            "Resumen", "Sobregasto PDV / CECO", "Detalle Cuenta", "Detalle PDV", "Detalle CECO", "Contraloria", "Cierres",
            "Temporalidad", "Comparación de Versiones", "Tabla Dinámica", "Cashi AI Assistant"
        ],
        "permite_descargar": True,
        "permite_admin": False,
    },
    "analista": {
        "nombre": "Analista Financiero",
        "secciones": ["Resumen", "Sobregasto PDV / CECO", "Detalle Cuenta", "Detalle PDV", "Detalle CECO", "Temporalidad"],
        "permite_descargar": False,
        "permite_admin": False,
    }
}


class AuthService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AuthService, cls).__new__(cls)
            cls._instance.users = {}
            cls._instance._mtime = 0
            cls._instance._cargar_usuarios()
        return cls._instance

    def _cargar_usuarios(self):
        if not USUARIOS_JSON.exists():
            self.users = {}
            return
        try:
            cur_mtime = USUARIOS_JSON.stat().st_mtime
            if cur_mtime != self._mtime:
                self.users = json.loads(USUARIOS_JSON.read_text(encoding="utf-8-sig"))
                self._mtime = cur_mtime
        except (OSError, json.JSONDecodeError):
            pass  # se conserva el último catálogo válido cargado en memoria

    def _hash(self, raw_pwd):
        return hashlib.sha256((SALT + raw_pwd).encode("utf-8")).hexdigest()

    def authenticate(self, username, password):
        """Autenticación estricta: usuario debe existir (match exacto,
        insensible a mayúsculas) y el hash del password debe coincidir.
        Cualquier otro caso devuelve None (acceso denegado)."""
        if not username or not password:
            return None

        self._cargar_usuarios()
        username_clean = str(username).strip()
        password_clean = str(password).strip()
        if not username_clean or not password_clean:
            return None

        u_lower = username_clean.lower()
        matched_key = None
        user_data = None
        for k, v in self.users.items():
            if str(k).strip().lower() == u_lower:
                matched_key = k
                user_data = v
                break

        if not user_data:
            return None

        stored_hash = user_data.get("password", "")
        if not stored_hash or self._hash(password_clean) != stored_hash:
            return None

        user_role = user_data.get("rol", user_data.get("role", "analista"))
        def_secciones = ROLES_PERMISOS.get(user_role, ROLES_PERMISOS["analista"])["secciones"]

        return {
            "username": matched_key,
            "nombre_completo": user_data.get("nombre_completo", matched_key),
            "role": user_role,
            "secciones": user_data.get("secciones", def_secciones),
            "permite_descargar": user_data.get("permite_descargar", user_role != "analista")
        }

    def get_all_users(self):
        """Retorna lista normalizada de todos los usuarios registrados"""
        self._cargar_usuarios()
        out = []
        for u_id, u_info in self.users.items():
            role = u_info.get("rol", u_info.get("role", "analista"))
            secciones = u_info.get("secciones", ROLES_PERMISOS.get(role, ROLES_PERMISOS["analista"])["secciones"])
            out.append({
                "username": u_id,
                "nombre_completo": u_info.get("nombre_completo", u_id),
                "rol": role,
                "permite_descargar": bool(u_info.get("permite_descargar", True)),
                "secciones": secciones,
                "creado": u_info.get("creado", "2026-08-06")
            })
        return sorted(out, key=lambda x: (0 if x["rol"] == "admin" else 1, x["username"]))

    def add_or_update_user(self, username, password=None, nombre_completo=None, rol="analista", permite_descargar=True, secciones=None):
        """Crea o actualiza un usuario y persiste a disco en datos/usuarios.json"""
        self._cargar_usuarios()
        u_clean = str(username).strip()
        if not u_clean:
            return False, "El nombre de usuario no puede estar vacío."

        user_entry = self.users.get(u_clean, {})
        if nombre_completo:
            user_entry["nombre_completo"] = str(nombre_completo).strip()
        elif "nombre_completo" not in user_entry:
            user_entry["nombre_completo"] = u_clean

        user_entry["rol"] = rol if rol in ROLES_PERMISOS else "analista"
        user_entry["permite_descargar"] = bool(permite_descargar)

        if secciones is not None:
            user_entry["secciones"] = list(secciones)
        elif "secciones" not in user_entry:
            user_entry["secciones"] = ROLES_PERMISOS.get(rol, ROLES_PERMISOS["analista"])["secciones"]

        if password and str(password).strip():
            if len(str(password).strip()) < 8:
                return False, "El password debe tener al menos 8 caracteres."
            user_entry["password"] = self._hash(str(password).strip())
        elif "password" not in user_entry:
            return False, "Debe especificar un password para un usuario nuevo."

        if "creado" not in user_entry:
            from datetime import datetime
            user_entry["creado"] = datetime.now().strftime("%Y-%m-%d")

        self.users[u_clean] = user_entry
        self._guardar_usuarios()
        return True, f"Usuario '{u_clean}' guardado exitosamente."

    def delete_user(self, username):
        """Elimina un usuario y persiste a disco"""
        self._cargar_usuarios()
        u_clean = str(username).strip()
        if u_clean in self.users:
            if u_clean.lower() == "admin1":
                return False, f"No está permitido eliminar al superadministrador '{u_clean}'."
            del self.users[u_clean]
            self._guardar_usuarios()
            return True, f"Usuario '{u_clean}' eliminado exitosamente."
        return False, f"El usuario '{u_clean}' no existe."

    def _guardar_usuarios(self):
        try:
            USUARIOS_JSON.parent.mkdir(parents=True, exist_ok=True)
            USUARIOS_JSON.write_text(json.dumps(self.users, ensure_ascii=False, indent=2), encoding="utf-8-sig")
            self._mtime = USUARIOS_JSON.stat().st_mtime
            return True
        except OSError as e:
            print(f"[AUTH-SERVICE] Error guardando usuarios: {e}")
            return False
