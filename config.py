import os
from dotenv import load_dotenv

load_dotenv()

def safe_int(value: str | None, default: int = 0) -> int:
    """Convierte un valor a int de forma segura."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        print(f"⚠️  Advertencia: No se pudo convertir '{value}' a int, usando default {default}")
        return default


class Config:
    # Token y configuración base
    TOKEN = os.getenv('DISCORD_TOKEN')
    GUILD_ID = os.getenv('GUILD_ID')
    
    # IDs (con conversión segura a int)
    OWNER_ID = safe_int(os.getenv('OWNER_ID'), default=1308111735760097283)
    ADMIN_USER_ID = safe_int(os.getenv('ADMIN_USER_ID'), default=0)
    LOG_CHANNEL_ID = safe_int(os.getenv('LOG_CHANNEL_ID'), default=0)
    INSIDER_ROLE_ID = safe_int(os.getenv('INSIDER_ROLE_ID'), default=0)
    VIP_ROLE_ID = safe_int(os.getenv('VIP_ROLE_ID'), default=0)
    NEXUS_PLUS_ROLE_ID = safe_int(os.getenv('NEXUS_PLUS_ROLE_ID'), default=0)
    
    # Enlaces
    DOWNLOAD_LINK = os.getenv('DOWNLOAD_LINK', '')
    
    # Nombres de roles (fallback)
    INSIDER_ROLE_NAME = "Insiders"
    VIP_ROLE_NAME = "VIP"
    NEXUS_PLUS_ROLE_NAME = "Nexus+"
    
    # Colores para embeds (Azul y blanquito suave)
    COLOR_SUCCESS = 0x87CEFA  # Azul cielo claro
    COLOR_ERROR = 0xFF9999    # Rojo suave
    COLOR_INFO = 0xFFFFFF     # Blanco puro
    COLOR_WARNING = 0xFFE5B4  # Naranja/Durazno suave
    COLOR_NEXUS = 0xB0C4DE    # Azul acero claro (Soft blue)
    
    # Validación
    @classmethod
    def validate(cls):
        """Valida que las variables críticas estén configuradas."""
        errors = []
        
        if not cls.TOKEN:
            errors.append("❌ DISCORD_TOKEN no está configurado")
        
        if cls.OWNER_ID == 0:
            errors.append("⚠️  OWNER_ID no está configurado (algunas funciones de owner no funcionarán)")
        
        if cls.INSIDER_ROLE_ID == 0:
            print("⚠️  INSIDER_ROLE_ID no configurado, solo se usará el nombre del rol")
        
        if cls.VIP_ROLE_ID == 0:
            print("⚠️  VIP_ROLE_ID no configurado, solo se usará el nombre del rol")
        
        if errors:
            for error in errors:
                print(error)
            raise RuntimeError("Faltan variables críticas en .env")
        
        print("✅ Configuración validada correctamente")


# Validar al importar
if __name__ != "__main__":
    Config.validate()