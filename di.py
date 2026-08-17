#!/usr/bin/env python3
"""
🔍 Diagnóstico del Bot Nexus
Verifica que todo esté configurado correctamente
"""

import os
import sys
from dotenv import load_dotenv

print("=" * 60)
print("🔍 DIAGNÓSTICO DEL BOT NEXUS")
print("=" * 60)

# 1. Verificar .env
print("\n📋 1. Verificando archivo .env...")
if os.path.exists(".env"):
    print("   ✅ Archivo .env encontrado")
else:
    print("   ❌ Archivo .env NO encontrado")
    sys.exit(1)

# 2. Cargar variables
load_dotenv()
print("\n📦 2. Cargando variables de entorno...")

vars_criticas = {
    "DISCORD_TOKEN": "Token del bot",
    "GUILD_ID": "ID del servidor",
    "OWNER_ID": "ID del owner",
    "INSIDER_ROLE_ID": "ID del rol Insiders",
    "VIP_ROLE_ID": "ID del rol VIP",
}

missing = []
for var, desc in vars_criticas.items():
    value = os.getenv(var)
    if value:
        if var == "DISCORD_TOKEN":
            # Mostrar solo los primeros y últimos caracteres
            masked = value[:10] + "..." + value[-10:]
            print(f"   ✅ {var}: {masked}")
        else:
            print(f"   ✅ {var}: {value}")
    else:
        print(f"   ❌ {var}: NO CONFIGURADO")
        missing.append(var)

# 3. Intentar importar config
print("\n🔧 3. Intentando importar config.py...")
try:
    # Crear un config temporal para testing
    from dotenv import load_dotenv
    load_dotenv()
    
    def safe_int(value, default=0):
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    
    owner_id = safe_int(os.getenv('OWNER_ID'), 0)
    insider_id = safe_int(os.getenv('INSIDER_ROLE_ID'), 0)
    vip_id = safe_int(os.getenv('VIP_ROLE_ID'), 0)
    
    print(f"   ✅ OWNER_ID convertido a: {owner_id} (tipo: {type(owner_id).__name__})")
    print(f"   ✅ INSIDER_ROLE_ID convertido a: {insider_id} (tipo: {type(insider_id).__name__})")
    print(f"   ✅ VIP_ROLE_ID convertido a: {vip_id} (tipo: {type(vip_id).__name__})")
    
except Exception as e:
    print(f"   ❌ Error al procesar variables: {type(e).__name__}: {e}")
    sys.exit(1)

# 4. Verificar dependencias
print("\n📚 4. Verificando dependencias...")
dependencies = ["discord", "flask", "python-dotenv"]
for dep in dependencies:
    try:
        __import__(dep)
        print(f"   ✅ {dep}")
    except ImportError:
        print(f"   ❌ {dep} NO INSTALADO")

# 5. Resumen
print("\n" + "=" * 60)
if missing:
    print(f"⚠️  Variables faltantes: {', '.join(missing)}")
    print("\nSolución: Llena todas las variables en .env")
else:
    print("✅ CONFIGURACIÓN CORRECTA - El bot debería funcionar")

print("=" * 60)